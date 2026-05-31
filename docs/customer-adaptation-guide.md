# 企业适配开发指导

本文档面向 **客户企业的开发人员**，说明如何将 `orchestration-service` 集成到企业自己的环境中。

---

## 架构概述

`orchestration-service` 是一个 FastAPI 应用，通过 **Protocol 接口** 与企业的认证、权限、注册中心、配置中心等外部系统解耦。配置统一由 `ConfigManager` 管理，它是一个部署单元级别的配置工具，不从属于 `create_app`。

```
┌─────────────────────────────────────────────────┐
│                 uvicorn my_app:app               │
│  ┌───────────────────────────────────────────┐  │
│  │         mh_orchestration_service          │  │
│  │  ┌─────────┐  ┌────────┐  ┌───────────┐  │  │
│  │  │ Chat API │  │ Agents │  │ Sessions  │  │  │
│  │  └────┬────┘  └───┬────┘  └─────┬─────┘  │  │
│  │       │           │             │         │  │
│  │  ┌────▼───────────▼─────────────▼──────┐  │  │
│  │  │         Adapter Layer               │  │  │
│  │  │  (注入你的企业实现)                   │  │  │
│  │  └────▲───────────▲─────────────▲──────┘  │  │
│  └───────┼───────────┼─────────────┼─────────┘  │
│          │           │             │            │
│     ┌────┴───┐ ┌────┴────┐ ┌──────┴──────┐    │
│     │  企业   │ │ 企业权限 │ │ 企业配置中心 │    │
│     │  SSO   │ │ 系统     │ │ / Vault     │    │
│     └────────┘ └─────────┘ └─────────────┘    │
└─────────────────────────────────────────────────┘
```

---

## 开箱即用

安装后无需任何代码即可启动，内置默认 adapter 使用内存数据：

```bash
uvicorn mh_orchestration_service.main:app --port 8005
```

内置的默认用户（通过 `X-User-Id` 请求头或 `x-user-id` cookie 指定用户标识）：

| 用户标识 | 权限 |
|----------|------|
| `admin` | `use:agent:*`, `use:tool:*`, `use:scene:*` |
| `member` | `use:agent:triage`, `use:tool:calculator`, `use:scene:triage` |
| `user` | `use:agent:code-reviewer`, `use:agent:writer`, `use:tool:web_search`, `use:scene:code_review`, `use:scene:writing` |

内置的默认数据：
- **Agent**: `code-reviewer`, `writer`
- **Tool**: `web_search`
- **Scenario**: `code_review`, `writing`

### 内置 Agent 开关

内置 agent 通过 `ORCH_ENABLE_BUILTIN_AGENTS=true` 环境变量控制（默认 `false`）：
- **开箱即用**：设置为 `true`，服务暴露 `triage`、`code-reviewer`、`writer` 三个样例 agent
- **生产环境**：注释掉或设为 `false`，此时 `RegistryClient` 不返回任何数据，由企业通过 registry_provider lifespan hook 注入自己的注册中心
- 内置 agent 全部**本地执行**，不依赖任何外部 agent service

| Agent | 英文名 | 中文名 | 说明 |
|-------|--------|--------|------|
| `triage` | General Assistant | 通用助手 | 理解用户需求并路由到专业 agent |
| `code-reviewer` | Code Reviewer | 代码审查 | 分析代码缺陷、风格、安全、性能问题 |
| `writer` | Writing Assistant | 写作助手 | 撰写文章、邮件、报告等内容 |

> 内置 agent 的 system_prompt 支持中英文，根据前端传来的 `Accept-Language` 自动适配。

---

## 安装

### 前置条件

- Python ≥ 3.12
- pip 或 uv

### 安装包

从我们提供的交付包中安装：

```bash
pip install minimal_harness-0.6.1a2-py3-none-any.whl
pip install mh_orchestration_service-0.1.0-py3-none-any.whl
```

验证安装：

```bash
python -c "from mh_orchestration_service import create_app; print('OK')"
```

---

## 需要实现的 Adapter 接口

以下是所有可以注入的可选/必选接口。Adapter 实例需通过 **lifespan hook** 注入到 `create_app()`——每个 adapter 都有对应的命名 hook 参数（如 `token_verifier=my_hook`），hook 在应用启动时执行，负责创建 adapter 实例并挂载到 `app.state.adapters` 上。**未注入的接口会使用内置默认实现**（开发/演示友好，生产环境建议全部注入）。

### 1. UserAuthProvider（认证）

验证用户的 JWT/SAML/OIDC token，返回用户身份。

```python
from minimal_harness.auth import UserAuthProvider, UserIdentity

class MyUserAuthProvider(UserAuthProvider):
    async def verify(self, request: Any) -> UserIdentity | None:
        cookie = request.cookies.get("sessionid")
        if not cookie:
            return None
        user_info = await self._call_auth_service(cookie)
        return UserIdentity(
            user_id=user_info["employee_id"],
            username=user_info["name"],
            roles=user_info.get("roles", []),
            extra_data=user_info,
        )
```

| 方法 | 返回 | 说明 |
|------|------|------|
| `verify(request)` | `UserIdentity \| None` | 收到完整 HTTP Request，可读 Cookie/Header/调用外部 API |

### 2. PermissionChecker（权限校验）

校验用户是否有权限执行某个操作。

```python
from minimal_harness.auth import PermissionChecker

class MyPermissionChecker(PermissionChecker):
    async def get_permissions(self, user_id: str) -> list[str]:
        return ["agent:code_review:execute", "tool:run:*"]

    async def check(self, user_id: str, permission: str) -> bool:
        perms = await self.get_permissions(user_id)
        from minimal_harness.auth import match_permission
        return match_permission(perms, permission)
```

> `UserAuthProvider` 和 `PermissionChecker` 可以用同一个类实现（参考内置 `_DefaultAuthProvider`）。

### 3. RegistryProvider（Agent / Tool / Scenario 注册中心）

提供 Agent 元数据、Tool 定义和 Scenario 列表。

```python
from minimal_harness.adapters import RegistryProvider

class MyRegistry(RegistryProvider):
    async def get_agent(self, name: str) -> dict | None: ...
    async def list_agents(self) -> list[dict]: ...
    async def get_tool(self, name: str) -> dict | None: ...
    async def list_tools(self) -> list[dict]: ...
    async def get_scenario(self, scenario_id: str) -> dict | None: ...
    async def list_scenarios(self) -> list[dict]: ...
```

返回的 dict 结构：

**Agent:**
```json
{
  "name": "code_review",
  "display_name": "代码审查助手",
  "description": "对 Git 提交进行代码审查"
}
```

**Tool:**
```json
{
  "name": "run_tool",
  "display_name": "执行工具",
  "description": "在沙箱中运行命令行工具",
  "parameters": {
    "type": "object",
    "properties": {
      "command": {"type": "string"}
    },
    "required": ["command"]
  }
}
```

**Scenario:**
```json
{
  "id": "code_review",
  "name": "代码审查",
  "description": "对 MR 进行自动化代码审查",
  "agents": [{"name": "code_review"}]
}
```

### 4. OutboundAuthProvider（出站认证注入）

为远程 agent / tool 调用注入认证 header。当 orchestration-service 调用外部 agent 或 tool 的 HTTP 端点时，此 adapter 为出站请求添加身份凭证。

默认实现将当前请求的 ``Authorization: Bearer <token>`` 透传给下游。客户可替换为自定义逻辑（如服务间 HMAC 签名、mTLS 客户端证书等）。

```python
from mh_orchestration_service import OutboundAuthProvider


class MyOutboundAuthProvider(OutboundAuthProvider):
    async def get_headers(
        self,
        request: Any,
        target_url: str,
        target_type: str,  # "agent" | "tool"
    ) -> dict[str, str]:
        token = get_current_auth_token()
        # 替换为企业的服务间认证机制
        return {
            "Authorization": f"Bearer {token}",
            "X-Custom-Auth": await self._sign(request, target_url),
        }
```

| 方法 | 返回 | 说明 |
|------|------|------|
| `get_headers(request, target_url, target_type)` | `dict[str, str]` | 返回要注入到出站请求中的 header 字典 |

### 5. M2MAuthProvider（机机接口鉴权）

验证 ``POST /api/v1/agents/{name}/run`` 和 ``POST /api/v1/tools/*/execute``
等机机端点的调用方身份，并将身份信息注入出站 binding 的 HTTP 请求 header。

默认实现仅识别 ``X-User-Id`` header（chat 流程 ``create_runtime()``
自动携带的内部 loopback 标识），其他请求一律返回 ``None``（401）。
生产环境必须替换为基于 SOA 或其他机制的鉴权实现：

```python
from mh_orchestration_service import M2MAuthProvider


class MyM2MAuthProvider(M2MAuthProvider):
    async def authenticate(self, request: Any) -> str | None:
        # 通过 SOA 验证 Authorization header
        auth = request.headers.get("Authorization", "")
        app_info = await self._soa_verify(auth)
        if app_info is None:
            return None
        return app_info.app_id

    async def get_identity_headers(
        self, request: Any, identity: str
    ) -> dict[str, str]:
        # 向下游出站绑定注入身份标识
        return {"X-SOA-Token": await self._soa_sign(identity)}

    async def close(self) -> None:
        pass
```

| 方法 | 返回 | 说明 |
|------|------|------|
| `authenticate(request)` | `str \| None` | 返回 app_id 表示鉴权通过（拥有所有权限），``None`` 表示失败（401） |
| `get_identity_headers(request, identity)` | `dict[str, str]` | 返回注入到出站 binding 的身份 header，下游 M2MAuthProvider 通过 ``authenticate`` 接收 |

### 6. UserIdentity（用户身份扩展）

`UserIdentity` 支持 `extra_data: dict[str, Any]` 字段，可保留企业用户模型的全部字段：

```python
identity = UserIdentity(
    user_id="emp_12345",
    username="zhangsan",
    roles=["developer", "admin"],
    extra_data={
        "employee_id": "EMP-12345",
        "display_name": "张三",
        "email": "zhangsan@company.com",
        "department": "R&D",
        "avatar_url": "https://sso.company.com/avatar/12345",
    },
)
```

> 系统内部仅消费 `user_id` 字符串用于权限和 Session 归属。`extra_data` 可供未来扩展或自定义中间件使用。

### 7. ConfigProvider（外部配置 / 密钥管理，可选）

对接外部配置中心（Apollo / Nacos / Consul）或密钥管理（HashiCorp Vault / AWS Secrets Manager / 阿里云 KMS）。

**`ConfigProvider`** 是唯一的协议类型；`SecretResolver` 是其向后兼容的别名。
`ConfigManager` 接受两个 `ConfigProvider` 实例——分别用于普通配置和敏感配置（见下方代码示例）。

```python
from mh_orchestration_service import ConfigProvider

class ApolloConfigProvider(ConfigProvider):
    async def get(self, key: str) -> str | None:
        return await apollo_client.get_value(key)

class VaultSecretResolver(ConfigProvider):
    async def get(self, key: str) -> str | None:
        return await vault_client.read_secret(key)
```

---

## Per-Request 上下文

Adapter 接口中的方法（如 `RegistryProvider.list_agents()`、`PermissionChecker.check()`）默认不接收 HTTP 请求
对象。如果你的 Adapter 需要感知当前请求（例如按用户身份过滤数据、转发 token 给下游），可以通过
**`ContextVar`** 获取。

所有 per-request 上下文由 `@app.middleware("http")` 中间件在请求进入时自动初始化，请求结束时自动
清理，无需手动管理。

### 可用上下文 API

所有函数都可以从 `mh_orchestration_service` 顶层导入：

```python
from mh_orchestration_service import (
    get_current_request,     # 获取完整 Request 对象
    get_current_cookies,     # 获取当前请求的 Cookie 字典
    get_current_auth_token,  # 获取认证凭证（Bearer token / cookie）
    get_current_user_id,     # 获取已认证的用户 ID
    get_current_locale,      # 获取 Accept-Language（"zh" / "en"）
    get_current_trace_id,    # 获取链路追踪 ID（X-Request-Id 或自动生成）
)
```

| 函数 | 返回 | 数据来源 | 说明 |
|------|------|----------|------|
| `get_current_request()` | `Request \| None` | 中间件 | 完整的 FastAPI Request 对象，可在 Adapter 中检查任意 header / body |
| `get_current_cookies()` | `dict[str, str]` | `request.cookies` | Cookie 字典，等价于 `get_current_request().cookies` |
| `get_current_auth_token()` | `str` | `Authorization` 头 → cookie 回退 | 优先取 `Bearer <token>`，无则取 `sessionid` / `sid` / `token` cookie |
| `get_current_user_id()` | `str \| None` | `get_user_id()` 认证后缓存 | 在 `get_user_id()` 调用后可用，`None` 表示未认证 |
| `get_current_locale()` | `str` | `Accept-Language` 头 | 默认返回 `"zh"`，完整的 locale 解析仍使用 `parse_locale()` |
| `get_current_trace_id()` | `str` | `X-Request-Id` / `X-Trace-Id` 头，或自动生成 | 用于日志和分布式追踪 |

> `get_current_user_id()` 仅在请求经过认证后（`get_user_id()` 被调用）才返回有效值。如果 Adapter
> 在未认证的请求路径中被调用，返回 `None`。

### Adapter 中使用示例

```python
from minimal_harness.adapters import RegistryProvider
from mh_orchestration_service import get_current_user_id, get_current_auth_token


class MyRegistry(RegistryProvider):
    async def list_agents(self) -> list[dict]:
        user_id = get_current_user_id()
        token = get_current_auth_token()
        # 调用企业后端 API，带上用户认证信息
        return await self._http.get(
            "/api/agents",
            headers={"Authorization": f"Bearer {token}"},
            params={"user_id": user_id},
        )

    async def list_tools(self) -> list[dict]:
        locale = get_current_locale()
        # 按语言返回不同的 tool 描述
        ...
```

```python
from mh_orchestration_service import get_current_request, get_current_trace_id


class MyPermissionChecker(PermissionChecker):
    async def check(self, user_id: str, permission: str) -> bool:
        request = get_current_request()
        trace_id = get_current_trace_id()
        # IP 白名单 + 链路追踪
        client_ip = request.client.host if request and request.client else "unknown"
        logger.info("perm_check", extra={"trace_id": trace_id, "user_id": user_id, "ip": client_ip})
        ...
```

---

## 统一配置管理（ConfigManager）

`ConfigManager` 是一个 **部署单元级别的配置管理工具**，不从属于 `create_app`。它提供一个统一的解析管道：

```
每个字段的解析优先级:
  1. {PREFIX}_{FIELD} 环境变量（最高优先级）
  2. 敏感字段 → secret_resolver.get()（如果配置了）
  3. 非敏感字段 → config_provider.get()（如果配置了）
  4. 必填字段（无默认值）仍缺失 → ConfigError
  5. 可选字段（有默认值）仍缺失 → 使用模型默认值

> 注：`config_provider` 和 `secret_resolver` 均为 `ConfigProvider` 协议类型，可传入不同实例以区分配置源与密钥源。
```

> **重要：** 所有 Config 类（包括自定义的）**必须继承 `pydantic.BaseModel`**。`ConfigManager.resolve()` 在运行时通过 `model_fields` 读取字段定义，并使用 `**kwargs` 调用构造器。普通 Python 类会导致运行时错误。

### 基本用法

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from mh_orchestration_service import ConfigManager, ConfigSchema, create_app
from pydantic import BaseModel


class MyRegistryConfig(BaseModel):
    api_url: str = "http://localhost:8080"
    api_key: str = ""


config_mgr = ConfigManager(
    config_provider=ApolloConfigProvider(),    # 可选
    secret_resolver=VaultSecretResolver(),     # 可选
)


@asynccontextmanager
async def my_registry_provider(app: FastAPI):
    """lifespan hook: 应用启动时解析配置并挂载 adapter"""
    cfg = await config_mgr.resolve(
        MyRegistryConfig,
        prefix="my.registry",
        sensitive_fields={"api_key"},
    )
    app.state.adapters.registry_provider = MyRegistry(
        api_url=cfg.api_url, api_key=cfg.api_key,
    )
    yield


settings = await config_mgr.resolve(ConfigSchema, prefix="ORCH")

app = create_app(
    settings=settings,
    registry_provider=my_registry_provider,
)
```

### `resolve()` 方法细节

```python
async def resolve(
    self,
    schema_cls: type[T],           # T 必须是 BaseModel 子类
    *,
    prefix: str = "ORCH",          # env 前缀 / 远程配置 key 前缀
    sensitive_fields: set[str] | None = None,
    key_mapping: dict[str, str] | None = None,   # 远程配置 key 重映射
) -> T:
```

### 开箱即用（仅环境变量）

不传 `config_provider` / `secret_resolver` 时，`ConfigManager` 只从环境变量读取：

```python
config_mgr = ConfigManager()   # 无需参数
settings = await config_mgr.resolve(ConfigSchema, prefix="ORCH")
# 只读 ORCH_TOKEN_SECRET_KEY, ORCH_DB_TYPE 等 env var
```

### `ConfigSchema` 框架配置声明

`ConfigSchema` 继承自 `pydantic.BaseModel`，无默认值的字段为必填。部署方必须为所有必填字段赋值，否则 `ConfigManager` 启动时报错。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `db_type` | str | 是 | 数据库类型 (`sqlite` / `opengauss`) |
| `db_path` | str | 是 | SQLite 文件路径 |
| `db_host` | str | 否 | openGauss 主机 |
| `db_port` | int | 否 | 数据库端口 (默认 5432) |
| `db_name` | str | 否 | 数据库名 |
| `db_user` | str | 否 | 数据库用户 |
| `db_password` | str | 否 | 数据库密码（敏感） |
| `db_auto_schema` | bool | 否 | 自动建表 (默认 false) |
| `cors_origins` | list[str] | 否 | 跨域来源 |
| `llm_api_key` | str | 否 | LLM API Key（敏感） |
| `llm_base_url` | str | 否 | LLM 接口地址 |
| `llm_model` | str | 否 | LLM 模型名 |
| `enable_builtin_agents` | bool | 否 | 开箱即用演示开关（默认 false），开启后暴露内置 agent |
| `dev_mode` | bool | 否 | 开发模式（默认 false），开启后暴露 `/api/v1/dev/*` 路由和前端静态文件 |

### 为什么需要 ConfigManager？

| 之前（各自为政） | 之后（统一管理） |
|---|---|
| 框架用 `ConfigProvider` 读自身 `Settings`，给出默认值 | 框架声明 `ConfigSchema`，部署方全量赋值 |
| Adapter 自己在构造函数里读 env / 硬编码 | Adapter 也通过 `ConfigManager.resolve()` 拿到配置 |
| 两个管道，两份配置来源 | 一个管道，同一份配置中心 |
| 配置有默认值，生产容易漏配 | 必填项缺失直接报错，启动即暴露问题 |
| EnvConfigProvider 兜底导致默认值静默生效 | no provider = 跳过远程，env 没有就直接报错 |

---

## 开箱默认 Adapter 的调测方式

内置默认 adapter 接受可选参数，无需实现完整接口即可调测。

### 调整管理员权限

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from mh_orchestration_service.app import create_app
from mh_orchestration_service.config import ConfigSchema
from mh_orchestration_service.services.auth_client import _DefaultAuthProvider


@asynccontextmanager
async def my_auth_hook(app: FastAPI):
    auth = _DefaultAuthProvider(
        token_secret_key=settings.token_secret_key,
        permissions={
            "admin": ["*:*:*"],
            "user": ["use:agent:code-reviewer"],
        },
    )
    app.state.adapters.token_verifier = auth
    app.state.adapters.permission_checker = auth
    yield

settings = ConfigSchema(
    token_secret_key="dev-secret",
    db_type="sqlite",
    db_path="./test.db",
)
app = create_app(settings=settings, lifespan_hooks=[my_auth_hook])
```

### 调整注册中心数据

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from mh_orchestration_service.app import create_app
from mh_orchestration_service.config import ConfigSchema
from mh_orchestration_service.services.registry_client import RegistryClient


@asynccontextmanager
async def my_registry_provider(app: FastAPI):
    app.state.adapters.registry_provider = RegistryClient(
        agents=[{"name": "my-agent", "display_name": "My Agent", "description": "..."}],
        tools=[{"name": "my-tool", "display_name": "My Tool", "description": "..."}],
        scenarios=[],
    )
    yield

settings = ConfigSchema(
    token_secret_key="dev-secret",
    db_type="sqlite",
    db_path="./test.db",
)
app = create_app(settings=settings, registry_provider=my_registry_provider)
```

### 开箱即用

```bash
uvicorn mh_orchestration_service.main:app --port 8005
```

配置从 `.env` 文件和环境变量读取。必填配置缺失时服务启动失败并提示具体缺失项。

```python
from mh_orchestration_service.app import create_app
# main.py 内部等价于:
#   config_mgr = ConfigManager()
#   settings = asyncio.run(config_mgr.resolve(ConfigSchema, prefix="ORCH"))
#   app = create_app(settings=settings)
```

---

## 编写启动文件

创建一个 Python 文件（例如 `my_app.py`），通过 lifespan hook 注入 adapter：

```python
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from mh_orchestration_service import ConfigManager, ConfigSchema, create_app
from pydantic import BaseModel
from my_adapters import (
    MyUserAuthProvider,
    MyPermissionChecker,
    MyRegistry,
    MyRegistryConfig,
    ApolloConfigProvider,
    VaultSecretResolver,
)


# ── 1. 统一配置管理 ──────────────────────────────
config_mgr = ConfigManager(
    config_provider=ApolloConfigProvider(),
    secret_resolver=VaultSecretResolver(),
)


# ── 2. 定义 Adapter lifespan hooks ──────────────
@asynccontextmanager
async def my_token_verifier(app: FastAPI):
    app.state.adapters.token_verifier = MyUserAuthProvider()
    yield


@asynccontextmanager
async def my_permission_checker(app: FastAPI):
    app.state.adapters.permission_checker = MyPermissionChecker()
    yield


@asynccontextmanager
async def my_registry_provider(app: FastAPI):
    cfg = await config_mgr.resolve(
        MyRegistryConfig,
        prefix="my.registry",
        sensitive_fields={"api_key"},
    )
    app.state.adapters.registry_provider = MyRegistry(
        api_url=cfg.api_url,
        api_key=cfg.api_key,
    )
    yield


@asynccontextmanager
async def my_outbound_auth_provider(app: FastAPI):
    app.state.adapters.outbound_auth_provider = MyOutboundAuthProvider()
    yield


@asynccontextmanager
async def my_m2m_auth_provider(app: FastAPI):
    app.state.adapters.m2m_auth_provider = MyM2MAuthProvider()
    yield


# ── 3. 解析框架配置（仅此一处需要 asyncio.run） ──
settings = asyncio.run(config_mgr.resolve(ConfigSchema, prefix="ORCH"))


# ── 4. 自定义 Logger（可选） ─────────────────────
logger = logging.getLogger("my_app")
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
))
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)


# ── 5. 组装应用 ──────────────────────────────────
app = create_app(
    settings=settings,
    token_verifier=my_token_verifier,
    permission_checker=my_permission_checker,
    registry_provider=my_registry_provider,
    outbound_auth_provider=my_outbound_auth_provider,
    m2m_auth_provider=my_m2m_auth_provider,
    logger=logger,
)
```

此时 `my_app.py` 就是你的 FastAPI 应用入口。Adapter 的配置解析延迟到应用启动时在 lifespan 中执行，无需在模块层为 adapter 调用 `asyncio.run()`。

---

## 启动服务

```bash
uvicorn my_app:app --host 0.0.0.0 --port 8005
```

生产环境建议：

```bash
uvicorn my_app:app \
  --host 0.0.0.0 \
  --port 8005 \
  --workers 4 \
  --log-level info \
  --no-access-log
```

---

## 配置管理

### 解析优先级

`ConfigManager.resolve()` 对每个字段独立按以下顺序查找：

1. **环境变量** `{PREFIX}_{FIELD}`（如 `ORCH_TOKEN_SECRET_KEY`）— 最高优先级
2. **外接配置** — 敏感字段走 `secret_resolver.get()`，非敏感字段走 `config_provider.get()`（两者均为 `ConfigProvider` 协议）
3. **字段默认值** — `ConfigSchema` 中定义的默认值（仅可选字段）
4. **必填字段仍缺失** → 抛出 `ConfigError`，启动失败

### 关键环境变量（prefix = `ORCH`）

| 变量 | 说明 | 必填 |
|------|------|------|
| `ORCH_DB_TYPE` | 数据库类型：`sqlite` 或 `opengauss` | 是 |
| `ORCH_DB_PATH` | SQLite 路径 | 是 |
| `ORCH_DB_HOST` | openGauss/PostgreSQL 主机 | 否 |
| `ORCH_DB_PORT` | openGauss/PostgreSQL 端口 (默认 5432) | 否 |
| `ORCH_DB_NAME` | openGauss/PostgreSQL 数据库名 | 否 |
| `ORCH_DB_USER` | openGauss/PostgreSQL 用户名 | 否 |
| `ORCH_DB_PASSWORD` | openGauss/PostgreSQL 密码 | 否 |
| `ORCH_DB_AUTO_SCHEMA` | 启动时自动建表 (默认 false) | 否 |
| `ORCH_CORS_ORIGINS` | 跨域来源 | 否 |
| `ORCH_LLM_API_KEY` | LLM API Key | 否 |
| `ORCH_LLM_BASE_URL` | LLM 接口地址 | 否 |
| `ORCH_LLM_MODEL` | LLM 模型名 | 否 |
| `ORCH_ENABLE_BUILTIN_AGENTS` | 内置 agent 开关（默认 false，设为 true 启用样例 agent） | 否 |

---

## 数据库

### SQLite（开发/轻量）

设置 `ORCH_DB_TYPE=sqlite`，系统自动在 `ORCH_DB_PATH` 位置创建数据库文件。

### openGauss（生产推荐）

设置 `ORCH_DB_TYPE=opengauss`，需要确保 `async-gaussdb` 已安装（已包含在依赖中）。

系统启动时会自动建表，包含统一的审计字段：

| 审计字段 | 类型 | 说明 |
|---------|------|------|
| `created_by` | TEXT | 创建人 |
| `last_updated_by` | TEXT | 最后更新人 |
| `creation_date` | TIMESTAMP(3) WITH TIME ZONE | 创建时间 |
| `last_update_date` | TIMESTAMP(3) WITH TIME ZONE | 最后更新时间 |
| `delete_flag` | CHAR(1) | 软删除标记（N/Y） |
| `last_update_trace_id` | TEXT | 分布式链路追踪 ID |

---

## API 清单

| 端点 | 方法 | 说明 |
|------|------|------|
| `GET /api/v1/auth/me` | GET | 当前用户信息 |
| `GET /api/v1/scenarios` | GET | 获取场景列表（按权限过滤） |
| `GET /api/v1/scenarios/{id}` | GET | 场景详情 |
| `POST /api/v1/chat/{memory_id}` | POST | SSE 流式聊天（支持 `session_id` 续传） |
| `GET /api/v1/sessions` | GET | 用户 Session 列表 |
| `POST /api/v1/sessions` | POST | 创建 Session |
| `GET /api/v1/sessions/{id}` | GET | Session 详情 + 消息历史 |
| `DELETE /api/v1/sessions/{id}` | DELETE | 删除 Session |
| `GET /api/v1/agents` | GET | Agent 列表（按权限过滤） |
| `POST /api/v1/agents/{name}/run` | POST | 运行 Agent（M2M 鉴权） |
| `GET /api/v1/tools` | GET | Tool 列表（按权限过滤） |
| `/docs` / `/redoc` | GET | Swagger / ReDoc 在线文档 |

---

## 最佳实践

1. **先写 `MyRegistry`** — 它是核心数据来源，正确实现后其他部分验证更方便
2. **使用 ConfigMapping** — 避免在代码中硬编码配置 key，将所有 key 集中管理
3. **生产环境务必注入所有 Adapter** — 不要依赖内置默认实现
4. **修改 `ORCH_TOKEN_SECRET_KEY`** — 默认值仅用于开发，生产环境必须改为你的密钥
5. **日志** — 注入自定义 logger 后，`orchestration.*` 下的所有日志会继承你的 handler。logger 不是 lifespan hook，它作为普通同步参数直接传入 `create_app()`
6. **审计** — 审计日志通过 `orchestration.audit` logger 输出（INFO 级别），可在日志系统中单独采集
7. **所有 Config 类必须继承 `pydantic.BaseModel`** — `ConfigManager` 在运行时依赖 `model_fields` 和 Pydantic 构造器
