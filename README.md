# orchestration-service — 编排网关

核心网关服务，依赖 [minimal-harness](../minimal-harness/) SDK。负责场景加载、用户权限校验、事件流归集，协调前端与各 worker 服务的通信。

- 端口：`8005`
- Swagger：`http://localhost:8005/docs`

> **开发者指南**：[docs/dev-guide.md](./docs/dev-guide.md)（中文） · [docs/dev-guide.agent.md](./docs/dev-guide.agent.md)（英文，面向 Coding Agent）
>
> **企业适配指导**：[docs/customer-adaptation-guide.md](./docs/customer-adaptation-guide.md)（中文） · [docs/customer-adaptation-guide.agent.md](./docs/customer-adaptation-guide.agent.md)（英文，面向 Coding Agent）
>
> **构建分发**：[docs/build-guide.md](./docs/build-guide.md)

## 适配层架构

orchestration 通过 Protocol 接口与外部系统解耦。所有适配器通过 `create_app()` 参数注入：

| 接口 | 默认实现 | 企业部署替换 |
|------|----------|----------|
| `UserAuthProvider` | `_DefaultAuthProvider` — 提取 `X-User-Id` header/cookie | 实现 `verify(request) → UserIdentity` |
| `PermissionChecker` | `_DefaultAuthProvider` — 内置权限表 (`admin` / `user`) | 实现 `check/get_permissions` |
| `RegistryProvider` | `RegistryClient` — 内置数据（受 `dev_mode` 控制） | 实现 `get_agent/list_agents/get_tool/list_tools/get_scenario/list_scenarios` |
| `CredentialVerifier` | `_DefaultCredentialVerifier` — 内存用户表 (`admin/admin`) | 实现 `verify_credentials(username, password) → UserIdentity` |
| `OutboundAuthProvider` | `_DefaultOutboundAuthProvider` — 透传请求 header | 实现 `get_headers(request, url, type) → dict` |
| `M2MAuthProvider` | `_DefaultM2MAuthProvider` — 信任 `X-User-Id` | 实现 `authenticate(request) → str\|None` |
| `ConfigProvider` | 无（仅环境变量） | 实现 `get(key) → str` 对接 Apollo/Nacos/Vault 等（`SecretResolver` 是向后兼容别名） |
| `LLMProvider` | `OpenAILLMProvider` — 通过环境变量 `ORCH_LLM_*` 配置 | 注入自定义 `llm_provider_factory` |

Protocol 定义见 [minimal-harness SDK](../minimal-harness/)。

## `create_app()` 工厂函数（客户部署入口）

`create_app()` 是 orchestration-service 的唯一入口，所有适配器通过参数注入：

```python
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from mh_orchestration_service import (
    ConfigManager, ConfigSchema, create_app,
)
from my_adapters import (
    CorpUserAuthProvider, CorpPermissionChecker, CorpRegistry,
)

# 1. 解析配置（env → 可选配置中心 → 报错）
config_mgr = ConfigManager()
settings = asyncio.run(config_mgr.resolve(ConfigSchema, prefix="ORCH"))

# 2. 配置 root logger（可选，不配置则使用 SDK 内置默认日志）
root = logging.getLogger()
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
))
root.addHandler(handler)
root.setLevel(logging.DEBUG)

# 3. 定义 Adapter LifespanHook（应用启动时注入）
@asynccontextmanager
async def token_verifier(app: FastAPI):
    app.state.adapters.token_verifier = CorpUserAuthProvider()
    yield

@asynccontextmanager
async def permission_checker(app: FastAPI):
    app.state.adapters.permission_checker = CorpPermissionChecker()
    yield

@asynccontextmanager
async def registry_provider(app: FastAPI):
    app.state.adapters.registry_provider = CorpRegistry()
    yield

# 4. 注入你的企业适配器
app = create_app(
    settings=settings,
    token_verifier=token_verifier,
    permission_checker=permission_checker,
    registry_provider=registry_provider,
)
```

省略的适配器参数会使用内置默认实现，适合开发和演示。

部署后以 `uvicorn my_app:app` 启动。

## AppState — 运行时可访问适配器

所有注入的适配器实例通过 `request.app.state.adapters` 访问：

```python
from mh_orchestration_service import AppState

adapters: AppState = request.app.state.adapters
identity = await adapters.token_verifier.verify(request)
perms = await adapters.permission_checker.get_permissions(user_id)
agents = await adapters.registry_provider.list_agents()
adapters.logger.info("Custom logger in use")  # 仅当注入了 Logger
```

## API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/scenarios` | GET | 场景列表（按权限过滤） |
| `/api/v1/chat/{memory_id}` | POST | SSE 流式聊天（支持 `session_id` 续传） |
| `/api/v1/sessions` | GET | 当前用户的 Session 列表（支持 `?scenario=` 过滤） |
| `/api/v1/sessions/{id}` | GET | Session 详情 + 消息历史 |
| `/api/v1/sessions/{id}` | DELETE | 删除 Session |
| `/api/v1/agents` | GET | Agent 列表（按权限过滤） |
| `/api/v1/tools` | GET | Tool 列表（按权限过滤） |
| `/health` | GET | 健康检查（始终返回 `{"status":"ok"}`） |
| `/ready` | GET | 就绪检查（始终返回 `{"status":"ready"}`） |
| `/api/v1/metrics` | GET | 运行时指标快照（仅 `metrics_enabled=true` 时注册） |

## AuditMiddleware

每个 Agent 执行周期自动记录审计日志（包括 `agent_start/end`、`llm_start/end`、`tool_start/end/error`、token 用量）。
日志级别为 `INFO`，可通过 `orchestration.audit` logger 配置。

## AccessLogMiddleware

每个 HTTP 请求自动输出一条结构化 JSON 访问日志，包含 `method`、`path`、`status`、`duration_ms`、`trace_id`、`user_id` 等字段。
日志级别为 `INFO`，可通过 `orchestration.access` logger 配置。

## 监控指标

当 `metrics_enabled=true` 时，服务会自动注册 `MetricsCollector`，在内存中采集如下指标并通过后台定时任务推送到日志：

| 指标 | 类型 | 标签 |
|------|------|------|
| `http_requests_total` | Counter | method, path, status |
| `http_request_duration_ms` | Histogram | method, path |
| `llm_requests_total` | Counter | provider, model, status |
| `llm_tokens_total` | Counter | provider, model, type (prompt/completion) |
| `llm_request_duration_ms` | Histogram | provider, model |
| `agent_runs_total` | Counter | agent_id, status |
| `tool_calls_total` | Counter | tool_name, status |
| `sessions_active` | Gauge | — |

指标通过 AuditMiddleware 的生命周期钩子自动采集。可通过 `/api/v1/metrics` 获取实时快照。

## PermissionMiddleware

每个 Agent 运行时自动校验工具调用权限。可通过 `check(user_id, perm)` 返回 `bool` 实现自定义逻辑。

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ORCH_TOKEN_SECRET_KEY` | — | **必填** JWT 签名密钥 |
| `ORCH_DB_TYPE` | `sqlite` | 数据库类型：`sqlite` 或 `opengauss` |
| `ORCH_DB_PATH` | `./orchestration.db` | SQLite 数据库文件路径 |
| `ORCH_DB_HOST` | `127.0.0.1` | openGauss/PostgreSQL 主机地址 |
| `ORCH_DB_PORT` | `5432` | openGauss/PostgreSQL 端口 |
| `ORCH_DB_NAME` | `orchestration` | openGauss/PostgreSQL 数据库名 |
| `ORCH_DB_USER` | `orchestration` | openGauss/PostgreSQL 用户名 |
| `ORCH_DB_PASSWORD` | `` | openGauss/PostgreSQL 密码 |
| `ORCH_DB_AUTO_SCHEMA` | `true` | 启动时自动建表（生产环境 openGauss 建议设为 `false`） |
| `ORCH_CORS_ORIGINS` | `["http://localhost:5173"]` | 跨域源 |
| `ORCH_LLM_API_KEY` | `` | LLM API Key |
| `ORCH_LLM_BASE_URL` | `` | LLM 接口地址（如 `https://api.openai.com/v1`） |
| `ORCH_LLM_MODEL` | `` | LLM 模型名 |
| `ORCH_METRICS_ENABLED` | `false` | 启用指标采集（计数器/直方图/仪表盘）及 `/api/v1/metrics` 端点 |
| `ORCH_METRICS_PUSH_INTERVAL` | `60` | 指标推送间隔（秒），仅 `ORCH_METRICS_ENABLED=true` 时生效 |
| `ORCH_DEV_MODE` | `false` | 开发模式开关，开启后暴露内置 agent、开发调试工具端点及 SSO 登录页 |
| `ORCH_ENABLE_FRONTEND` | `false` | 内置前端 UI 开关，开启后 FastAPI 在 `/` 直接 serve 编译后的 SPA |

### 客户侧数据库部署（openGauss）

设置 `ORCH_DB_TYPE=opengauss` 并配置数据库连接信息。

> **建表控制**：默认启动时自动建表。生产环境如 DBA 严格管理表结构，设置 `ORCH_DB_AUTO_SCHEMA=false` 关闭自动建表。

表结构包含统一的审计字段：

| 审计字段 | 类型 | 说明 |
|---------|------|------|
| `created_by` | TEXT | 创建人 |
| `last_updated_by` | TEXT | 最后更新人 |
| `creation_date` | TIMESTAMP(3) WITH TIME ZONE | 创建时间（毫秒精度） |
| `last_update_date` | TIMESTAMP(3) WITH TIME ZONE | 最后更新时间（毫秒精度） |
| `delete_flag` | CHAR(1) | 删除标记：`N` 未删除，`Y` 已删除（软删除） |
| `last_update_trace_id` | TEXT | 更新追踪 ID（用于分布式链路追踪） |

所有业务主键使用程序生成的 BIGINT（snowflake-like 方案），不依赖数据库自增。

## 外部配置对接

当客户有自己的配置中心（Apollo/Nacos/Consul）和密钥系统（HashiCorp Vault/AWS Secrets Manager）时，可通过 `ConfigProvider` 协议对接（`SecretResolver` 是其向后兼容别名）。

### 解析优先级

配置值按以下优先级解析（高 → 低）：

1. **环境变量**（`ORCH_*`）— 最高优先级，运维可临时覆盖
2. **外接配置**（`ConfigProvider` 实例，敏感与非敏感通过不同实例区分）— 来自配置中心
3. **代码默认值** — 若以上均未设置，使用 `Settings` 中的默认值

### ConfigMapping — 变量映射

```python
from mh_orchestration_service import ConfigMapping

mapping = ConfigMapping(
    # 内部变量名 → 客户配置中心的 key
    key_mapping={
        "auth_service_url":   "woa.orchestration.auth.service.url",
    },
    # 标记为敏感的 key，会走 secret_resolver 实例而非 config_provider 实例
    sensitive_keys={},
)
```

客户可将此 JSON 化存于自己的配置平台，在启动时加载。

### 实现自定义 UserAuthProvider（对接企业 SSO）

`verify()` 收到的是完整的 FastAPI `Request`，可读 Cookie/Header/调外部 API：

```python
from typing import Any
from minimal_harness.auth import UserAuthProvider, UserIdentity

class CorpSSOVerifier(UserAuthProvider):
    async def verify(self, request: Any) -> UserIdentity | None:
        # 1. 从 Cookie 中提取会话标识
        session_id = request.cookies.get("sessionid")
        if not session_id:
            return None
        # 2. 调用企业认证 API（request 还可读其他 header/query）
        user_info = await self._call_auth_api(session_id)
        if not user_info:
            return None
        # 3. 返回标准身份（extra_data 保留完整信息）
        return UserIdentity(
            user_id=user_info["employee_id"],
            username=user_info["name"],
            roles=user_info.get("roles", []),
            extra_data=user_info,
        )
```

> HTTP Bearer token 由内置 `_DefaultAuthProvider` 从 `request.headers["authorization"]` 提取，客户使用 Cookie 时直接在 `verify()` 中读取 `request.cookies` 即可。

### 实现其他 Provider

```python
from mh_orchestration_service import ConfigProvider

class ApolloConfigProvider(ConfigProvider):
    async def get(self, key: str) -> str | None:
        return await apollo_client.get_value(key)

class VaultSecretResolver(ConfigProvider):
    async def get(self, key: str) -> str | None:
        return await vault_client.read_secret(key)
```

## 内置 Agent 样例 & 开发模式

设置 `ORCH_DEV_MODE=true` 后，服务会暴露 3 个内置样例 agent 以及开发调试用工具端点：

| Agent | 功能 | 说明 |
|-------|------|------|
| `triage` | 通用助手 | 理解用户需求并路由到专业 agent（code-reviewer / writer） |
| `code-reviewer` | 代码审查 | 分析代码变更中的缺陷、风格、安全和性能问题 |
| `writer` | 写作助手 | 辅助撰写文章、邮件、报告等 |

内置 agent 是**本地执行**（无 endpoint_url），不依赖任何外部服务。所有 agent 通过 `minimal-harness` SDK 的 `AgentRuntime` 在进程内运行。

> **生产环境**请确保 `ORCH_DEV_MODE` 为 `false`（默认值），并通过 `registry_provider` 参数注入企业自己的注册中心实现。

## 内置前端 UI（一站式部署）

设置 `ORCH_ENABLE_FRONTEND=true` 后，FastAPI 会在 `/` 直接 serve 编译后的 SPA（单页应用），无需额外部署前端服务器或 Nginx 代理。

```bash
# 构建前端（SPA + 组件 bundle → 复制到 static/）
bash scripts/build-frontend.sh

# 启动（前端在 http://localhost:8005）
bash scripts/dev-standalone.sh
```

或使用 `bash scripts/dev-standalone.sh` 自动完成上述流程。

> **生产环境**确保 `ORCH_ENABLE_FRONTEND` 为 `false`（默认值），由企业自己的 Nginx / CDN 负责前端静态资源分发。

## 本地开发

```bash
# 带前端（先构建前端 SPA + 复制到 static/）
bash scripts/dev-standalone.sh

# 或仅后端（前端由 Vite 开发服务器提供热更新）
uv run -m uvicorn mh_orchestration_service.main:app --port 8005
cd web-frontend && npm run dev
```

或使用项目根目录的 `bash scripts/dev.sh` 一键启动所有服务。

## 构建分发

```bash
cd packages/orchestration-service
uv build
# 产出 dist/mh_orchestration_service-*.whl
```

客户 pip install 后，编写自己的启动文件注入适配器即可。

## 测试

```bash
uv run pytest packages/orchestration-service/tests -v
```
