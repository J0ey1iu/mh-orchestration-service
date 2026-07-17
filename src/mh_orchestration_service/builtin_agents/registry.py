from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from mh_orchestration_service.context import get_current_request, get_current_user_id


async def _web_search_fn(query: str = "") -> AsyncIterator[Any]:
    await asyncio.sleep(0.05)
    yield f'[Simulated] Search results for "{query}": Found 42 relevant pages. Top result: example.com/{query.replace(" ", "-")}'


async def _discover_agents_fn(
    exclude: str = "", locale: str = ""
) -> AsyncIterator[Any]:
    from mh_orchestration_service.api.locale import (
        resolve_description,
        resolve_display_name,
    )
    from mh_orchestration_service.auth import match_permission

    request = get_current_request()
    if request is None:
        yield {"status": "ok", "agents": []}
        return
    adapters = request.app.state.adapters
    identity = get_current_user_id() or ""

    agents = await adapters.management_provider.list_agents()
    user_perms: list[str] | None = None
    if adapters.permission_checker is not None:
        user_perms = await adapters.permission_checker.get_permissions(identity)

    result = []
    for a in agents:
        name = a["name"]
        if exclude and name == exclude:
            continue
        if user_perms is not None and not match_permission(
            user_perms, f"use:agent:{name}"
        ):
            continue
        result.append(
            {
                "name": a["name"],
                "display_name": resolve_display_name(
                    a.get("display_name", a["name"]),
                    a.get("display_name_locale"),
                    locale,
                ),
                "description": resolve_description(
                    a.get("description", ""),
                    a.get("description_locale"),
                    locale,
                ),
            }
        )
    yield {"status": "ok", "agents": result}


async def _handoff_fn(
    target_agent_name: str = "",
    context_summary: str = "",
    task_description: str = "",
    locale: str = "",
) -> AsyncIterator[Any]:
    if not target_agent_name:
        yield {"status": "error", "message": "target_agent_name is required"}
        return

    from mh_orchestration_service.services.database import get_session_store
    from mh_orchestration_service.services.runtime_service import (
        acquire_session_lock,
        create_runtime,
        release_session_lock,
    )

    request = get_current_request()
    if request is None:
        yield {"status": "error", "message": "No request context"}
        return

    identity = get_current_user_id() or ""
    store = await get_session_store()

    import uuid

    handoff_session_id = f"mem_{uuid.uuid4().hex[:12]}"
    await store.create_session(
        session_id=handoff_session_id,
        agent_name=target_agent_name,
        user_id=identity,
        transient=True,
    )

    lock = await acquire_session_lock(handoff_session_id)
    sub_task = None
    sub_stop_event = None
    result_text = ""
    try:
        runtime, _agent_registry, _tool_registry, _ = await create_runtime(
            request=request,
            user_id=identity,
            agent_name=target_agent_name,
            tool_names=[],
            session_store=store,
            session_id=handoff_session_id,
        )

        combined = f"Context: {context_summary}\n\nTask: {task_description}"

        sub_task, sub_stop_event, queue = await runtime.run(
            user_input=[{"type": "text", "text": combined}],
            agent_metadata_id=target_agent_name,
            memory_id=handoff_session_id,
            context={"locale": locale, "agent_name": target_agent_name},
        )

        yield {
            "status": "progress",
            "type": "handoff_started",
            "message": f"Starting delegated task to {target_agent_name}...",
            "target_agent": target_agent_name,
            "task": task_description,
        }

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if sub_stop_event and sub_stop_event.is_set():
                    yield {"status": "error", "type": "interrupted"}
                    break
                continue

            if event is None:
                break

            from minimal_harness.types import (
                AgentEnd,
                ToolProgress,
            )

            if isinstance(event, AgentEnd):
                result_text = event.response or result_text
            elif isinstance(event, ToolProgress):
                yield {
                    "status": "progress",
                    "type": "tool_progress",
                    "tool_call": event.tool_call,
                    "chunk": event.chunk,
                }

        if result_text:
            yield {
                "status": "handoff_complete",
                "type": "handoff_complete",
                "message": "Delegated task completed",
                "result": result_text,
                "target_agent": target_agent_name,
            }
        else:
            yield {
                "status": "handoff_complete",
                "type": "handoff_complete",
                "message": "Delegated task completed",
                "target_agent": target_agent_name,
            }
    finally:
        if sub_stop_event is not None:
            sub_stop_event.set()
        if sub_task is not None:
            sub_task.cancel()
            try:
                await sub_task
            except (asyncio.CancelledError, Exception):
                pass
        await release_session_lock(handoff_session_id, lock)


TRIAGE_SYSTEM_PROMPT = """You are a general assistant that helps users with various tasks. Understand the user's request and either handle it directly or route it to the right specialist agent.

Available specialist agents:
- code-reviewer — Analyzes code changes for bugs, style issues, security vulnerabilities, and performance problems
- writer — Helps craft clear, engaging, and well-structured content including articles, emails, reports, and creative writing

When a user's request matches a specialist's expertise, use the handoff tool to transfer the conversation to them.
For general knowledge questions or information lookup, use the web_search tool to find answers online."""

TRIAGE_SYSTEM_PROMPT_ZH = """你是一个通用助手，帮助用户处理各种任务。理解用户的需求，直接处理或将其路由到合适的专业智能体。

可用的专业智能体：
- code-reviewer — 分析代码变更中的缺陷、风格问题、安全漏洞和性能问题
- writer — 帮助撰写清晰、有吸引力且结构良好的内容，包括文章、邮件、报告和创意写作

当用户的需求符合某个专业智能体的专长时，使用 handoff 工具将对话移交给该智能体。
对于一般的知识问答或信息查询，使用 web_search 工具在线搜索答案。"""

CODE_REVIEW_SYSTEM_PROMPT = """You are a senior code reviewer. Analyze code changes for bugs, style issues, security vulnerabilities, and performance problems. Provide constructive, specific feedback with concrete fix suggestions. Be thorough but concise.
When the user's request is outside your expertise, use handoff to transfer to another agent. Use discover_agents to find available agents first."""

CODE_REVIEW_SYSTEM_PROMPT_ZH = """你是一名资深代码审查专家。分析代码变更中的缺陷、风格问题、安全漏洞和性能问题。给出有建设性的具体反馈，并提供修复建议。做到既全面又简洁。
当用户的需求超出你的专业范围时，使用 handoff 将任务移交给其他智能体。先使用 discover_agents 查找可用智能体。"""

WRITER_SYSTEM_PROMPT = """You are a professional writing assistant. Help users craft clear, engaging, and well-structured content including articles, emails, reports, and creative writing. Provide thoughtful suggestions and improvements.
When the user's request is outside your expertise, use handoff to transfer to another agent. Use discover_agents to find available agents first."""

WRITER_SYSTEM_PROMPT_ZH = """你是一名专业的写作助手。帮助用户撰写清晰、有吸引力且结构良好的内容，包括文章、邮件、报告和创意写作。提供周到的建议和改进方案。
当用户的需求超出你的专业范围时，使用 handoff 将任务移交给其他智能体。先使用 discover_agents 查找可用智能体。"""
