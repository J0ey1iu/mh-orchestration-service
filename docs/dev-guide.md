# orchestration-service 服务开发指南

本文档面向 **基于 orchestration-service 框架开发自定义网关服务** 的开发人员。详细说明如何集成适配器、管理配置、扩展功能。

---

## 架构概述

```
┌──────────────────────────────────────────────────────────┐
│                   uvicorn my_app:app                       │
│  ┌────────────────────────────────────────────────────┐   │
│  │              mh_orchestration_service                │   │
│  │  ┌──────────┐ ┌───────────┐ ┌──────────────────┐   │   │
│  │  │ Chat API │ │ Agents    │ │ Sessions / Auth   │   │   │
│  │  └────┬─────┘ └─────┬─────┘ └────────┬─────────┘   │   │
│  │       │              │                │             │   │
│  │  ┌────▼──────────────▼────────────────▼──────────┐  │   │
│  │  │              Adapter Layer                     │  │   │
│  │  │  (通过 LifespanHook 注入自定义实现)              │  │   │
│  │  └────▲──────────────▲────────────────▲──────────┘  │   │
│  └───────┼──────────────┼────────────────┼────────────┘   │
│          │              │                │                  │
│    ┌─────┴────┐  ┌──────┴──────┐  ┌──────┴──────┐        │
│    │  企业 SSO │  │ 企业权限系统  │  │ 企业配置中心   │        │
│    └──────────┘  └─────────────┘  └─────────────┘        │
└──────────────────────────────────────────────────────────┘
```

### 核心概念

| 概念 | 说明 |
|------|------|
| `create_app()` | 框架唯一入口工厂函数，接收 `settings` + Adapter LifespanHook |
| `LifespanHook` | async context manager, 在 FastAPI 生命周期内初始化和清理 Adapter |
| `AppState` | 运行时 Adapter 容器，挂载在 `app.state.adapters` |
| `ConfigManager` | 配置管理工具，支持环境变量 + 配置中心 + 密钥管理三层回退 |
| `Per-request Context` | 通过 ContextVar 暴露当前请求信息，Adapter 内可直接获取 |

---

## 开发步骤

### 1. 创建启动文件

`my_app.py` 是唯一的入口文件：

```python
import asyncio
import logging
from contextlib import asynccontextmanager
from mh_orchestration_service import ConfigManager, ConfigSchema, create_app
from pydantic import BaseModel


# ── 1. 定义 Adapter 配置（可选） ───────────────
class MyRegistryConfig(BaseModel):
    api_url: str = ""
    api_key: str = ""


# ── 2. 创建 ConfigManager ─────────────────────
config_mgr = ConfigManager()  # 仅从环境变量读取


# ── 3. 定义 LifespanHook ──────────────────────
@asynccontextmanager
async def my_registry_provider(app):
    app.state.adapters.registry_provider = MyRegistry()
    yield


# ── 4. 解析框架配置 ───────────────────────────
settings = asyncio.run(config_mgr.resolve(ConfigSchema, prefix="ORCH"))


# ── 5. 组装应用 ───────────────────────────────
app = create_app(
    settings=settings,
    registry_provider=my_registry_provider,
)
```

### 2. 实现 Adapter 接口

#### UserAuthProvider — 用户认证

```python
from minimal_harness.auth import UserAuthProvider, UserIdentity

class MyAuth(UserAuthProvider):
    async def verify(self, request) -> UserIdentity | None:
        token = request.headers.get("authorization", "")
        user_info = await self._call_sso(token)
        if not user_info:
            return None
        return UserIdentity(
            user_id=user_info["uid"],
            username=user_info["name"],
            roles=user_info.get("roles", []),
            extra_data=user_info,
        )
```

#### PermissionChecker — 权限校验

```python
from minimal_harness.auth import PermissionChecker, match_permission

class MyPerms(PermissionChecker):
    async def get_permissions(self, user_id: str) -> list[str]:
        return ["use:agent:*", "use:tool:calculator"]

    async def check(self, user_id: str, permission: str) -> bool:
        perms = await self.get_permissions(user_id)
        return match_permission(perms, permission)
```

权限格式: `action:resource:target`，支持 `*` 通配符。

#### RegistryProvider — 注册中心

```python
from minimal_harness.adapters import RegistryProvider

class MyRegistry(RegistryProvider):
    async def list_agents(self) -> list[dict]:
        return [{"name": "my-agent", "display_name": "My Agent", "description": "..."}]

    async def get_agent(self, name: str) -> dict | None: ...
    async def list_tools(self) -> list[dict]: ...
    async def get_tool(self, name: str) -> dict | None: ...
    async def list_scenarios(self) -> list[dict]: ...
    async def get_scenario(self, s_id: str) -> dict | None: ...
```

返回数据结构:

| 对象 | 键 | 说明 |
|------|-----|------|
| Agent | `name`, `display_name`, `description`, `display_name_locale`, `description_locale`, `system_prompt`, `system_prompt_locale`, `endpoint_url` | `endpoint_url` 非空则视为远程 Agent |
| Tool | `name`, `display_name`, `description`, `parameters`, `display_name_locale`, `description_locale`, `endpoint_url`, `_fn` | `parameters` 为 JSON Schema；`endpoint_url` 非空则远程执行，否则本地执行 `_fn` |
| Scenario | `id`, `name`, `description`, `agents`, `name_locale`, `icon` | `agents` 为 `[{name, tool_names}]` |

#### OutboundAuthProvider — 出站认证注入

```python
from mh_orchestration_service import OutboundAuthProvider

class MyOutboundAuth(OutboundAuthProvider):
    async def get_headers(self, request, target_url: str, target_type: str) -> dict[str, str]:
        return {"Authorization": request.headers.get("authorization", "")}
```

默认实现将当前请求的所有 header（不含 hop-by-hop）透传给下游。

#### M2MAuthProvider — 机机鉴权

```python
from mh_orchestration_service import M2MAuthProvider

class MyM2MAuth(M2MAuthProvider):
    async def authenticate(self, request) -> str | None:
        auth = request.headers.get("Authorization", "")
        app_id = await self._verify_service_token(auth)
        return app_id  # None → 401

    async def get_identity_headers(self, request, identity: str) -> dict[str, str]:
        return {"X-SOA-Token": await self._sign(identity)}

    async def close(self): ...
```

#### ConfigProvider — 外部配置 / 密钥管理

`ConfigProvider` 是唯一的协议类型；`SecretResolver` 是其向后兼容别名。
`ConfigManager` 接受两个 `ConfigProvider` 实例，分别用于普通配置和敏感配置。

```python
from mh_orchestration_service import ConfigProvider

class ApolloConfigProvider(ConfigProvider):
    async def get(self, key: str) -> str | None:
        return await apollo_client.get_value(key)

class VaultSecretResolver(ConfigProvider):
    async def get(self, key: str) -> str | None:
        return await vault_client.read_secret(key)
```

### 3. 使用 Per-request Context

Adapter 内可直接获取当前请求上下文：

```python
from mh_orchestration_service import (
    get_current_request,     # 完整 Request 对象
    get_current_cookies,     # Cookie 字典
    get_current_auth_token,  # Bearer token / cookie 回退
    get_current_user_id,     # 已认证用户 ID
    get_current_locale,      # Accept-Language
    get_current_trace_id,    # 追踪 ID
)

class MyRegistry(RegistryProvider):
    async def list_agents(self) -> list[dict]:
        user_id = get_current_user_id()
        token = get_current_auth_token()
        return await self._http.get("/api/agents", headers={"Authorization": f"Bearer {token}"})
```

### 4. 完整集成示例

```python
import asyncio, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel
from mh_orchestration_service import (
    ConfigManager, ConfigSchema, ConfigMapping, create_app,
    OutboundAuthProvider, M2MAuthProvider,
)
from minimal_harness.auth import UserAuthProvider, PermissionChecker
from minimal_harness.adapters import RegistryProvider


# ── 日志 ────────────────────────────────────────
logger = logging.getLogger("my_app")
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.DEBUG)


# ── 配置中心 ────────────────────────────────────
class MyConfigProvider(ConfigProvider):
    async def get(self, key: str) -> str | None: ...

config_mgr = ConfigManager(
    config_provider=MyConfigProvider(),
    secret_resolver=MyConfigProvider(),  # 同一协议类型，可传不同实例
)

mapping = ConfigMapping(
    key_mapping={"llm_api_key": "woa.orchestration.llm.key"},
    sensitive_keys={"llm_api_key"},
)


# ── 自定义 Adapter 配置 ────────────────────────
class CorpRegistryConfig(BaseModel):
    api_url: str = "http://registry:8080"
    api_token: str = ""


# ── Adapter 实现 ────────────────────────────────
class CorpAuth(UserAuthProvider):
    async def verify(self, request): ...

class CorpPerms(PermissionChecker):
    async def get_permissions(self, user_id): ...

class CorpRegistry(RegistryProvider):
    def __init__(self, api_url, api_token): ...
    async def list_agents(self): ...


# ── Lifespan Hooks ──────────────────────────────
@asynccontextmanager
async def token_verifier(app: FastAPI):
    app.state.adapters.token_verifier = CorpAuth()
    yield

@asynccontextmanager
async def permission_checker(app: FastAPI):
    app.state.adapters.permission_checker = CorpPerms()
    yield

@asynccontextmanager
async def registry_provider(app: FastAPI):
    cfg = await config_mgr.resolve(CorpRegistryConfig, prefix="CORP.REGISTRY", sensitive_fields={"api_token"})
    app.state.adapters.registry_provider = CorpRegistry(cfg.api_url, cfg.api_token)
    yield

@asynccontextmanager
async def outbound_auth(app: FastAPI):
    app.state.adapters.outbound_auth_provider = MyOutboundAuth()
    yield


# ── 组装 ────────────────────────────────────────
settings = asyncio.run(config_mgr.resolve(ConfigSchema, prefix="ORCH"))
app = create_app(
    settings=settings,
    token_verifier=token_verifier,
    permission_checker=permission_checker,
    registry_provider=registry_provider,
    outbound_auth_provider=outbound_auth,
    logger=logger,
)
```

### 5. 启动

```bash
uvicorn my_app:app --host 0.0.0.0 --port 8005 --workers 4
```

---

## 配置管理

### ConfigManager 解析优先级

每个字段独立按以下顺序查找：

1. 环境变量 `{PREFIX}_{FIELD}`（最高优先级）
2. 敏感字段 → `secret_resolver.get()`
3. 非敏感字段 → `config_provider.get()`
4. 模型默认值（仅可选字段）
5. 必填字段仍缺失 → `ConfigError`，启动失败

### ConfigSchema 关键字段

| 字段 | 环境变量 | 必填 | 说明 |
|------|---------|------|------|
| `db_type` | `ORCH_DB_TYPE` | 是 | `sqlite` 或 `opengauss` |
| `db_path` | `ORCH_DB_PATH` | 是 | SQLite 文件路径 |
| `db_host` | `ORCH_DB_HOST` | 否 | openGauss 主机 |
| `db_port` | `ORCH_DB_PORT` | 否 | 默认 5432 |
| `db_name` | `ORCH_DB_NAME` | 否 | 数据库名 |
| `db_user` | `ORCH_DB_USER` | 否 | 数据库用户 |
| `db_password` | `ORCH_DB_PASSWORD` | 否 | 敏感字段 |
| `cors_origins` | `ORCH_CORS_ORIGINS` | 否 | 逗号分隔 |
| `llm_api_key` | `ORCH_LLM_API_KEY` | 否 | 敏感字段 |
| `llm_base_url` | `ORCH_LLM_BASE_URL` | 否 | LLM 接口地址 |
| `llm_model` | `ORCH_LLM_MODEL` | 否 | 模型名 |
| `enable_builtin_agents` | `ORCH_ENABLE_BUILTIN_AGENTS` | 否 | 默认 false |
| `dev_mode` | `ORCH_DEV_MODE` | 否 | 默认 false |

---

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/auth/me` | 当前用户信息 |
| GET | `/api/v1/scenarios` | 场景列表（按权限过滤） |
| GET | `/api/v1/scenarios/{id}` | 场景详情 |
| POST | `/api/v1/chat/{memory_id}` | SSE 流式聊天 |
| GET | `/api/v1/sessions` | 用户 Session 列表 |
| POST | `/api/v1/sessions` | 创建 Session |
| GET | `/api/v1/sessions/{id}` | Session 详情 |
| DELETE | `/api/v1/sessions/{id}` | 删除 Session |
| GET | `/api/v1/agents` | Agent 列表 |
| POST | `/api/v1/agents/{name}/run` | M2M 鉴权的 Agent 执行 |
| GET | `/api/v1/tools` | Tool 列表 |
| POST | `/api/v1/tools/{name}/execute` | M2M 鉴权的 Tool 执行 |

---

## 数据库

- SQLite（开发）: `ORCH_DB_TYPE=sqlite`
- openGauss（生产）: `ORCH_DB_TYPE=opengauss`

所有表包含统一审计字段：`created_by`, `last_updated_by`, `creation_date`, `last_update_date`, `delete_flag`(N/Y), `last_update_trace_id`。

`ORCH_DB_AUTO_SCHEMA` 控制是否自动建表，生产环境可设为 `false` 由 DBA 管理。

---

## 最佳实践

1. **先实现 RegistryProvider** — 它是核心数据来源，正确后其他部分验证更方便
2. **所有 Adapter 都用 LifespanHook 注入** — 不要依赖内置默认实现上生产
3. **Config 类必须继承 pydantic.BaseModel** — ConfigManager 依赖 `model_fields`
4. **ORCH_TOKEN_SECRET_KEY 必须修改** — 默认仅用于开发
5. **内置 agent 仅用于演示** — 生产环境 `ORCH_ENABLE_BUILTIN_AGENTS=false`
6. **SSE 流协议** — Chat API 使用 SSE 流式推送事件，前端监听 `message` 事件
7. **日志审计** — 审计日志通过 `orchestration.audit` logger（INFO 级别）输出
