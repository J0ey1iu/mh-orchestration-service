from __future__ import annotations

import logging
import os
import warnings
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from minimal_harness.adapters import MetadataManager, RegistryProvider
from minimal_harness.auth import PermissionChecker, UserAuthProvider
from minimal_harness.client.logging_setup import setup_service_logging
from minimal_harness.llm.factory import register_builtin_providers
from minimal_harness.llm.llm import LLMProvider, LLMProviderRegistry
from minimal_harness.types import ExtraHeadersProvider
from starlette.responses import FileResponse

from mh_orchestration_service.api.auth_routes import dev_router
from mh_orchestration_service.api.component_sources import component_sources_router
from mh_orchestration_service.api.router import router
from mh_orchestration_service.config import ConfigSchema
from mh_orchestration_service.context import (
    clear_current_user_id,
    ensure_trace_id,
    reset_current_request,
    reset_current_trace_id,
    set_current_request,
    set_current_trace_id,
)
from mh_orchestration_service.monitoring.middleware import AccessLogMiddleware
from mh_orchestration_service.services.auth_client import _DefaultAuthProvider
from mh_orchestration_service.services.generated_agent_provider import (
    AgentGenerator,
    DefaultAgentGenerator,
)
from mh_orchestration_service.services.generated_tool_provider import (
    DefaultToolGenerator,
    ToolGenerator,
)
from mh_orchestration_service.services.m2m_auth import (
    M2MAuthProvider,
    _DefaultM2MAuthProvider,
)
from mh_orchestration_service.services.management_provider import (
    InMemoryManagementProvider,
)
from mh_orchestration_service.services.outbound_auth import (
    OutboundAuthProvider,
    _DefaultOutboundAuthProvider,
)

logger = logging.getLogger("orchestration.app")


def _load_provider_defaults(prefix: str = "ORCH_PROVIDER") -> dict[str, dict[str, Any]]:
    """Scan env vars and build provider default configs.

    Env var naming: ``{PREFIX}_{PROVIDER}__{KEY}``
    e.g. ``ORCH_PROVIDER_OPENAI__API_KEY=sk-xxx``.
    """
    result: dict[str, dict[str, Any]] = {}
    marker = f"{prefix}_"
    sep = "__"
    for key, value in os.environ.items():
        if not key.startswith(marker):
            continue
        suffix = key[len(marker) :]
        if sep not in suffix:
            continue
        provider, cfg_key = suffix.split(sep, 1)
        provider = provider.lower()
        cfg_key = cfg_key.lower()
        result.setdefault(provider, {})[cfg_key] = value
    return result


LifespanHook = Callable[[FastAPI], AbstractAsyncContextManager[None]]
"""生命周期钩子类型。

接收 FastAPI app，以 async generator 形式执行初始化和清理。
用法::

    @asynccontextmanager
    async def my_hook(app: FastAPI):
        cfg = await my_config_mgr.resolve(MyCfg, prefix="MY")
        app.state.adapters.registry_provider = MyRegistryProvider(cfg)
        yield
        await app.state.adapters.registry_provider.close()
"""


class AppState:
    """Holder for adapter instances, attached to app.state.adapters."""

    def __init__(
        self,
        settings: ConfigSchema,
        token_verifier: UserAuthProvider | None = None,
        permission_checker: PermissionChecker | None = None,
        registry_provider: RegistryProvider | None = None,
        management_provider: MetadataManager | None = None,
        llm_provider_factory: Callable[[], LLMProvider] | None = None,
        outbound_auth_provider: OutboundAuthProvider | None = None,
        m2m_auth_provider: M2MAuthProvider | None = None,
        llm_extra_headers_provider: ExtraHeadersProvider | None = None,
        generated_tool_provider: ToolGenerator | None = None,
        generated_agent_provider: AgentGenerator | None = None,
        llm_provider_registry: LLMProviderRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.token_verifier = token_verifier
        self.permission_checker = permission_checker
        self.registry_provider = registry_provider
        self.management_provider: MetadataManager | None = management_provider
        self.llm_provider_factory = llm_provider_factory
        self.outbound_auth_provider = outbound_auth_provider
        self.m2m_auth_provider = m2m_auth_provider
        self.llm_extra_headers_provider = llm_extra_headers_provider
        self.generated_tool_provider = generated_tool_provider
        self.generated_agent_provider = generated_agent_provider
        self.llm_provider_registry = llm_provider_registry


def _fill_default_adapters(state: AppState) -> None:
    """Fill any None adapters with built-in defaults."""
    if state.token_verifier is None and state.permission_checker is None:
        auth = _DefaultAuthProvider()
        state.token_verifier = auth
        state.permission_checker = auth
    else:
        if state.token_verifier is None:
            state.token_verifier = _DefaultAuthProvider()
        if state.permission_checker is None:
            state.permission_checker = _DefaultAuthProvider()
    if state.management_provider is None:
        if state.registry_provider is None:
            state.management_provider = InMemoryManagementProvider(
                enable_builtin=state.settings.dev_mode,
            )
            state.registry_provider = state.management_provider
        elif isinstance(state.registry_provider, MetadataManager):
            state.management_provider = state.registry_provider
        else:
            state.management_provider = state.registry_provider  # type: ignore[assignment]
            logger.warning(
                "registry_provider is not a MetadataManager; "
                "management CRUD APIs will not be available"
            )
    if state.outbound_auth_provider is None:
        state.outbound_auth_provider = _DefaultOutboundAuthProvider()
    if state.m2m_auth_provider is None:
        state.m2m_auth_provider = _DefaultM2MAuthProvider()

    if state.llm_provider_registry is None:
        registry = LLMProviderRegistry()
        register_builtin_providers(registry)
        registry.clone_factory("openai", "openai_viz")
        for name, cfg in _load_provider_defaults().items():
            registry.set_default_config(name, cfg)
        if registry.get_default_config("openai"):
            registry.set_default_config(
                "openai_viz", registry.get_default_config("openai")
            )
        state.llm_provider_registry = registry

    if state.llm_provider_factory is None:
        _registry = state.llm_provider_registry

        def _default_llm_factory() -> LLMProvider:
            return _registry.create("openai", {})

        state.llm_provider_factory = _default_llm_factory

    # Must come after llm_provider_factory default (above).
    if state.generated_tool_provider is None:
        state.generated_tool_provider = DefaultToolGenerator()
        state.generated_tool_provider.set_llm_factory(state.llm_provider_factory)

    if state.generated_agent_provider is None:
        state.generated_agent_provider = DefaultAgentGenerator()
        state.generated_agent_provider.set_llm_factory(state.llm_provider_factory)


async def _close_adapters(state: AppState) -> None:
    """Close built-in adapters that were created by ``_fill_default_adapters``."""
    if isinstance(state.token_verifier, _DefaultAuthProvider):
        await state.token_verifier.close()
    if (
        isinstance(state.permission_checker, _DefaultAuthProvider)
        and state.permission_checker is not state.token_verifier
    ):
        await state.permission_checker.close()
    if isinstance(state.outbound_auth_provider, _DefaultOutboundAuthProvider):
        await state.outbound_auth_provider.close()
    if isinstance(state.m2m_auth_provider, _DefaultM2MAuthProvider):
        await state.m2m_auth_provider.close()
    if isinstance(state.management_provider, InMemoryManagementProvider):
        await state.management_provider.close()


def create_app(
    *,
    settings: ConfigSchema,
    logger: logging.Logger | None = None,
    token_verifier: LifespanHook | None = None,
    permission_checker: LifespanHook | None = None,
    management_provider: LifespanHook | None = None,
    llm_provider_factory: LifespanHook | None = None,
    outbound_auth_provider: LifespanHook | None = None,
    m2m_auth_provider: LifespanHook | None = None,
    llm_extra_headers_provider: ExtraHeadersProvider | None = None,
    generated_tool_provider: LifespanHook | None = None,
    generated_agent_provider: LifespanHook | None = None,
    llm_provider_registry: LifespanHook | None = None,
    lifespan_hooks: list[LifespanHook] | None = None,
) -> FastAPI:
    """Create a configured FastAPI app for the orchestration service.

    All adapters are initialized inside the FastAPI ``lifespan``:

    1. Built-in defaults are filled for every adapter slot.
    2. Named per-adapter hooks run in order (e.g. ``token_verifier``).
       Each hook receives the app with ``app.state.adapters`` already
       populated with defaults, and can override its slot.
    3. Generic ``lifespan_hooks`` run next, for cross-cutting concerns.
    4. Database is initialized.
    5. Application serves requests.
    6. On shutdown, hooks clean up in reverse order, then built-in
       adapters are closed.

    Args:
        settings: 已解析的框架配置（由 ConfigManager.resolve() 或手动构建）。
        logger: （已弃用）自定义 logger。请改为在调用 ``create_app()``
            之前自行配置 ``logging.getLogger()``（root logger）。
        token_verifier: 认证适配器 hook。
        permission_checker: 权限校验适配器 hook。
        management_provider: 统一数据管理适配器 hook。
        llm_provider_factory: LLM provider 工厂 hook。
        outbound_auth_provider: 出站认证适配器 hook。
        m2m_auth_provider: 机机接口鉴权适配器 hook。
        llm_extra_headers_provider: LLM 额外 HTTP 头回调，可动态返回
            headers 字典（如 ``x-reasoning-format``）。
        llm_provider_registry: LLM provider 注册表 hook。
            用于自定义 provider 注册（per-agent provider 选择）。
        lifespan_hooks: 通用生命周期钩子，在 per-adapter hooks 之后执行。
    """
    if logger is not None:
        warnings.warn(
            "create_app(logger=...) is deprecated. "
            "Configure logging.getLogger() (root logger) before calling create_app() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
    setup_service_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from mh_orchestration_service.services.database import get_db, init_db

        state = AppState(
            settings=settings,
            llm_extra_headers_provider=llm_extra_headers_provider,
        )
        _fill_default_adapters(state)
        app.state.adapters = state

        if settings.metrics_enabled:
            from mh_orchestration_service.monitoring.collector import (
                MetricsCollector,
                set_collector,
            )

            collector = MetricsCollector()
            set_collector(collector)
            collector.start_push(interval=settings.metrics_push_interval)
            logging.getLogger("orchestration.app").info(
                "Metrics collector started (push_interval=%ds)",
                settings.metrics_push_interval,
            )

        async with AsyncExitStack() as stack:
            if token_verifier is not None:
                await stack.enter_async_context(token_verifier(app))
            if permission_checker is not None:
                await stack.enter_async_context(permission_checker(app))
            if management_provider is not None:
                await stack.enter_async_context(management_provider(app))
            if llm_provider_factory is not None:
                await stack.enter_async_context(llm_provider_factory(app))
            if llm_provider_registry is not None:
                await stack.enter_async_context(llm_provider_registry(app))
            if outbound_auth_provider is not None:
                await stack.enter_async_context(outbound_auth_provider(app))
            if m2m_auth_provider is not None:
                await stack.enter_async_context(m2m_auth_provider(app))
            if generated_tool_provider is not None:
                await stack.enter_async_context(generated_tool_provider(app))
            if generated_agent_provider is not None:
                await stack.enter_async_context(generated_agent_provider(app))

            for hook in lifespan_hooks or []:
                await stack.enter_async_context(hook(app))

            await init_db(
                settings.database_url,
                auto_schema=settings.db_auto_schema,
            )
            yield

        if settings.metrics_enabled:
            from mh_orchestration_service.monitoring.collector import (
                get_collector,
                set_collector,
            )

            collector = get_collector()
            if collector:
                await collector.stop_push()
                set_collector(None)

        await _close_adapters(state)
        await get_db().close()

    app = FastAPI(title="Orchestration Service", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(AccessLogMiddleware)

    @app.middleware("http")
    async def request_context_middleware(request, call_next):
        req_token = set_current_request(request)
        trace_id = ensure_trace_id(request)
        trace_token = set_current_trace_id(trace_id)
        try:
            return await call_next(request)
        finally:
            reset_current_request(req_token)
            reset_current_trace_id(trace_token)
            clear_current_user_id()

    app.include_router(router)

    if settings.enable_eval:
        from mh_orchestration_service.eval import router as eval_router

        app.include_router(eval_router)

    if settings.dev_mode:
        app.include_router(dev_router)
        app.include_router(component_sources_router)

        from mh_orchestration_service.api.runtime_tools_dev import (
            router as dev_runtime_tools_router,
        )

        app.include_router(dev_runtime_tools_router)

        static_dir = Path(__file__).resolve().parent / "static"
        if static_dir.is_dir():
            app.mount(
                "/",
                StaticFiles(directory=str(static_dir), html=True),
                name="frontend",
            )

            @app.middleware("http")
            async def spa_fallback(request, call_next):
                response = await call_next(request)
                if (
                    response.status_code == 404
                    and request.method == "GET"
                    and request.url.path != "/api"
                    and not request.url.path.startswith("/api/")
                    and not request.url.path.startswith("/docs")
                    and not request.url.path.startswith("/openapi")
                    and not request.url.path.startswith("/redoc")
                ):
                    index_path = static_dir / "index.html"
                    if index_path.is_file():
                        return FileResponse(str(index_path))
                return response

    return app
