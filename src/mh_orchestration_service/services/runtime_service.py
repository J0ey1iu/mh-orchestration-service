from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Sequence
from typing import Any, Callable
from urllib.parse import quote

from fastapi import Request
from minimal_harness.agent.middleware import Middleware
from minimal_harness.agent.registry import AgentRegistry
from minimal_harness.agent.runtime import AgentRuntime
from minimal_harness.auth import match_permission
from minimal_harness.llm.llm import LLMProvider
from minimal_harness.memory_store import SessionStoreProtocol
from minimal_harness.tool.registry import ToolRegistry
from minimal_harness.types import (
    AgentMetadata,
    LocalAgentBinding,
    LocalToolBinding,
    RemoteAgentBinding,
    RemoteToolBinding,
    ToolMetadata,
)

from mh_orchestration_service.api.locale import parse_locale_json
from mh_orchestration_service.services.audit_middleware import AuditMiddleware
from mh_orchestration_service.services.database import get_session_store
from mh_orchestration_service.services.m2m_auth import M2MAuthProvider
from mh_orchestration_service.services.outbound_auth import OutboundAuthProvider
from mh_orchestration_service.services.perm_middleware import PermissionMiddleware

# ── Per-session concurrency lock ──────────────────────────────────────────────

_SESSION_LOCKS: dict[str, asyncio.Lock] = {}
_SESSION_LOCKS_MUTEX = asyncio.Lock()


async def acquire_session_lock(session_id: str) -> asyncio.Lock:
    """Get (or create) and acquire a per-session lock.

    This serialises writes to the same ``session_id`` so that concurrent
    chat requests cannot race on ``save_memory``.
    """
    async with _SESSION_LOCKS_MUTEX:
        if session_id not in _SESSION_LOCKS:
            _SESSION_LOCKS[session_id] = asyncio.Lock()
        lock = _SESSION_LOCKS[session_id]
    await lock.acquire()
    return lock


async def release_session_lock(session_id: str, lock: asyncio.Lock) -> None:
    """Release a per-session lock and prune it from the dictionary."""
    lock.release()
    async with _SESSION_LOCKS_MUTEX:
        _SESSION_LOCKS.pop(session_id, None)


def _make_extra_headers_provider(
    provider: OutboundAuthProvider,
    request: Request,
    target_url: str,
    target_type: str,
) -> Callable[[], Awaitable[dict[str, str]]]:
    """Return a closure that calls the provider at execution time."""

    async def _inner() -> dict[str, str]:
        return await provider.get_headers(request, target_url, target_type)

    return _inner


async def _agent_binding(
    meta: dict,
    request: Request | None = None,
    m2m_auth_provider: M2MAuthProvider | None = None,
    identity: str = "",
    outbound_auth_provider: OutboundAuthProvider | None = None,
    verify_agent_tool_ssl: bool = False,
) -> RemoteAgentBinding | LocalAgentBinding:
    if "endpoint_url" in meta and meta["endpoint_url"]:
        url = meta["endpoint_url"]
        if request and url.startswith("/"):
            url = str(request.base_url).rstrip("/") + url
        headers: dict[str, str] = {}
        if m2m_auth_provider is not None and request is not None and identity:
            headers = await m2m_auth_provider.get_identity_headers(request, identity)
        if request is not None and "x-user-id" not in headers:
            _xu = request.headers.get("x-user-id", "").strip()
            if _xu:
                headers["x-user-id"] = _xu
        extra_provider = None
        if outbound_auth_provider is not None and request is not None:
            extra_provider = _make_extra_headers_provider(
                outbound_auth_provider, request, url, "agent"
            )
        return RemoteAgentBinding(
            url=url,
            headers=headers,
            extra_headers_provider=extra_provider,
            verify_ssl=verify_agent_tool_ssl,
        )
    return LocalAgentBinding()


async def _tool_binding(
    meta: dict,
    name: str,
    request: Request | None = None,
    m2m_auth_provider: M2MAuthProvider | None = None,
    identity: str = "",
    outbound_auth_provider: OutboundAuthProvider | None = None,
    scenario_id: str = "",
    agent_name: str = "",
    verify_agent_tool_ssl: bool = False,
) -> RemoteToolBinding | LocalToolBinding:
    if "endpoint_url" in meta and meta["endpoint_url"]:
        url = meta["endpoint_url"]
        if request and url.startswith("/"):
            url = str(request.base_url).rstrip("/") + url
        if scenario_id and name in ("discover_agents", "handoff"):
            url = f"{url}?scenario_id={scenario_id}"
        if name == "discover_agents" and agent_name:
            url = f"{url}{'&' if '?' in url else '?'}agent_name={quote(agent_name, safe='')}"
        headers: dict[str, str] = {}
        if m2m_auth_provider is not None and request is not None and identity:
            headers = await m2m_auth_provider.get_identity_headers(request, identity)
        if request is not None and "x-user-id" not in headers:
            _xu = request.headers.get("x-user-id", "").strip()
            if _xu:
                headers["x-user-id"] = _xu
        extra_provider = None
        if outbound_auth_provider is not None and request is not None:
            extra_provider = _make_extra_headers_provider(
                outbound_auth_provider, request, url, "tool"
            )
        return RemoteToolBinding(
            url=url,
            headers=headers,
            extra_headers_provider=extra_provider,
            timeout=60.0,
            verify_ssl=verify_agent_tool_ssl,
        )
    fn = meta.get("_fn")
    return LocalToolBinding(fn=fn)


async def _get_permitted_scenario_agents(
    management_provider: Any,
    permission_checker: Any,
    scenario_id: str,
    user_id: str,
) -> set[str] | None:
    """Resolve scenario agents and intersect with user permissions.

    Returns ``None`` when *scenario_id* is empty (no filtering).
    Returns ``set[str]`` of agent names the user can access within the scenario.
    """
    if not scenario_id:
        return None

    scenario_agent_names: set[str] | None = None
    for s in await management_provider.list_scenarios():
        if s.get("id") == scenario_id:
            scenario_agent_names = {a["name"] for a in s.get("agents", [])}
            break
    if scenario_agent_names is None:
        return set()

    if permission_checker is not None:
        user_perms = await permission_checker.get_permissions(user_id)
        scenario_agent_names = {
            name
            for name in scenario_agent_names
            if match_permission(user_perms, f"use:agent:{name}")
        }
    return scenario_agent_names


async def create_runtime(
    request: Request,
    user_id: str,
    agent_name: str,
    tool_names: list[str],
    session_store: SessionStoreProtocol | None = None,
    session_id: str = "",
    scenario_id: str = "",
    trace_id: str = "",
    provider: str = "",
    model: str = "",
    emit_message_events: bool = True,
    extra_middleware: Sequence[Middleware] | None = None,
) -> tuple[AgentRuntime, AgentRegistry, ToolRegistry, SessionStoreProtocol]:
    adapters = request.app.state.adapters
    llm_provider_registry = getattr(adapters, "llm_provider_registry", None)
    llm_extra_headers = getattr(adapters, "llm_extra_headers_provider", None)
    outbound_auth_provider = getattr(adapters, "outbound_auth_provider", None)
    verify_agent_tool_ssl = getattr(adapters.settings, "verify_agent_tool_ssl", False)

    agent_registry = AgentRegistry()

    # ── Build agent→tool_names map from all scenarios ──
    scenario_tool_names: dict[str, list[str]] = {}
    for s in await adapters.management_provider.list_scenarios():
        for a in s.get("agents", []):
            name = a["name"]
            tools = a.get("tool_names", [])
            if name not in scenario_tool_names:
                scenario_tool_names[name] = list(tools)
            else:
                existing = set(scenario_tool_names[name])
                for t in tools:
                    if t not in existing:
                        scenario_tool_names[name].append(t)
                        existing.add(t)

    scenario_agent_names = await _get_permitted_scenario_agents(
        adapters.management_provider,
        adapters.permission_checker,
        scenario_id,
        user_id,
    )

    # When there is no scenario filter, check permissions per-agent
    user_perms: list[str] | None = None
    if scenario_agent_names is None:
        if adapters.permission_checker:
            user_perms = await adapters.permission_checker.get_permissions(user_id)
        # user_perms stays None when no permission_checker — all agents pass

    # Register agents — filtered by scenario + permissions
    for a in await adapters.management_provider.list_agents():
        name = a["name"]
        if scenario_agent_names is not None and name not in scenario_agent_names:
            continue
        if user_perms is not None and not match_permission(
            user_perms, f"use:agent:{name}"
        ):
            continue
        await agent_registry.register(
            AgentMetadata(
                name=a["name"],
                display_name=a.get("display_name", a["name"]),
                display_name_locale=parse_locale_json(a.get("display_name_locale")),
                description=a.get("description", ""),
                description_locale=parse_locale_json(a.get("description_locale")),
                system_prompt=a.get("system_prompt", ""),
                system_prompt_locale=parse_locale_json(a.get("system_prompt_locale")),
                metadata_id=a["name"],
                tool_names=scenario_tool_names.get(a["name"], []),
                provider=a.get("provider", "openai"),
                model=a.get("model", ""),
                llm_config=a.get("llm_config", {}),
                binding=await _agent_binding(
                    a,
                    request,
                    m2m_auth_provider=adapters.m2m_auth_provider,
                    identity=user_id,
                    outbound_auth_provider=outbound_auth_provider,
                    verify_agent_tool_ssl=verify_agent_tool_ssl,
                ),
            )
        )

    all_tool_names = set(tool_names)
    all_tool_names.update(scenario_tool_names.get(agent_name, []))

    tool_registry = ToolRegistry()
    for tname in all_tool_names:
        tool_meta = await adapters.management_provider.get_tool(tname)
        if tool_meta is None:
            continue
        params = tool_meta.get("parameters", {"type": "object", "properties": {}})
        await tool_registry.register(
            ToolMetadata(
                name=tool_meta["name"],
                display_name=tool_meta.get("display_name", tool_meta["name"]),
                display_name_locale=parse_locale_json(
                    tool_meta.get("display_name_locale")
                ),
                description=tool_meta.get("description", ""),
                description_locale=parse_locale_json(
                    tool_meta.get("description_locale")
                ),
                parameters=params,
                binding=await _tool_binding(
                    tool_meta,
                    tname,
                    request,
                    m2m_auth_provider=adapters.m2m_auth_provider,
                    identity=user_id,
                    outbound_auth_provider=outbound_auth_provider,
                    scenario_id=scenario_id,
                    agent_name=agent_name,
                    verify_agent_tool_ssl=verify_agent_tool_ssl,
                ),
            )
        )

    if session_store is None:
        session_store = await get_session_store()

    resolved_provider = provider
    resolved_model = model
    if not resolved_provider or not resolved_model:
        target_agent_meta = await agent_registry.get(agent_name)
        if target_agent_meta:
            if not resolved_provider:
                resolved_provider = target_agent_meta.provider
            if not resolved_model:
                resolved_model = target_agent_meta.model

    middleware: list[Middleware] = [
        PermissionMiddleware(user_id, adapters.permission_checker),
        AuditMiddleware(
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_name,
            scenario_id=scenario_id,
            provider=resolved_provider,
            model=resolved_model,
            trace_id=trace_id,
        ),
    ]
    if extra_middleware:
        middleware.extend(extra_middleware)

    llm_provider_resolver: Callable[[AgentMetadata], LLMProvider] | None = None
    if llm_provider_registry is not None:

        def _resolver(meta: AgentMetadata) -> LLMProvider:
            cfg: dict = {
                "model": meta.model,
                "_extra_headers_provider": llm_extra_headers,
            }
            cfg.update(meta.llm_config)
            return llm_provider_registry.create(meta.provider, cfg)

        llm_provider_resolver = _resolver
    else:
        llm_provider_factory = getattr(adapters, "llm_provider_factory", None)
        if llm_provider_factory is not None:

            def _fallback_resolver(meta: AgentMetadata) -> LLMProvider:  # noqa: ARG001
                return llm_provider_factory()

            llm_provider_resolver = _fallback_resolver

    assert llm_provider_resolver is not None, (
        "No LLM provider resolver or factory configured"
    )

    runtime = AgentRuntime(
        agent_registry=agent_registry,
        session_store=session_store,
        tool_registry=tool_registry,
        middleware=middleware,
        llm_provider_resolver=llm_provider_resolver,
        emit_message_events=emit_message_events,
    )

    return runtime, agent_registry, tool_registry, session_store
