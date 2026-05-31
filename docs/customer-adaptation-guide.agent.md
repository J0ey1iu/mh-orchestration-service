# Customer Adaptation Guide (for Code Agent)

This document specifies the exact implementation contract for adapting `orchestration-service` into a customer environment. Follow these instructions precisely — any deviation will cause runtime failures.

---

## 1. Repository Structure & Dependencies

Two packages must be installed (not on PyPI; delivered as `.whl`):

```
minimal_harness          # SDK: protocols, agent runtime, tool system, database, LLM
mh_orchestration_service # FastAPI web gateway, depends on minimal_harness
```

All customer adapter code lives in a **single Python file** (e.g. `my_app.py`) that constructs and exposes a FastAPI app via `create_app()`.

---

## 2. What You MUST Implement

### 2.1 UserAuthProvider (import: `from minimal_harness.auth import UserAuthProvider`)

```python
@runtime_checkable
class UserAuthProvider(Protocol):
    async def verify(self, request: Any) -> UserIdentity | None:
        ...
```

**Contract:**
- Input: raw FastAPI `Request` object
- Output: `UserIdentity` on success, `None` on invalid/missing credentials
- Read from `request.headers`, `request.cookies`, or call external auth API as needed
- `UserIdentity` dataclass: `from minimal_harness.auth import UserIdentity`

```python
@dataclass
class UserIdentity:
    user_id: str            # REQUIRED — used for permission checks & session ownership
    username: str = ""
    roles: list[str] = field(default_factory=list)
    extra_data: dict[str, Any] = field(default_factory=dict)
```

### 2.2 PermissionChecker (import: `from minimal_harness.auth import PermissionChecker`)

```python
@runtime_checkable
class PermissionChecker(Protocol):
    async def get_permissions(self, user_id: str) -> list[str]:
        ...
    async def check(self, user_id: str, permission: str) -> bool:
        ...
```

**Contract:**
- Permission format: `action:resource:target` (e.g. `"use:agent:code-reviewer"`)
- Wildcard `*` supported at any segment (e.g. `"use:agent:*"`)
- Use `match_permission()` from `minimal_harness.auth.protocols` to evaluate wildcards
- Return `True` if permission string matches user's permissions

### 2.3 RegistryProvider (import: `from minimal_harness.adapters import RegistryProvider`)

```python
@runtime_checkable
class RegistryProvider(Protocol):
    async def get_agent(self, name: str) -> dict[str, Any] | None: ...
    async def list_agents(self) -> list[dict[str, Any]]: ...
    async def get_tool(self, name: str) -> dict[str, Any] | None: ...
    async def list_tools(self) -> list[dict[str, Any]]: ...
    async def get_scenario(self, scenario_id: str) -> dict[str, Any] | None: ...
    async def list_scenarios(self) -> list[dict]: ...
```

**Contract:**
- Agent dict shape: `{"name": str, "display_name": str, "description": str}`
- Tool dict shape: `{"name": str, "display_name": str, "description": str, "parameters": {"type": "object", "properties": {...}, "required": [...]}}`
- Scenario dict shape: `{"id": str, "name": str, "description": str, "agents": [{"name": str}]}`

**Contract:**
- Return `UserIdentity` if password matches, `None` otherwise
- If not implemented, `POST /api/v1/auth/login` returns 501

### 2.5 ConfigProvider (optional; import: `from mh_orchestration_service.config_protocols import ConfigProvider`)

```python
@runtime_checkable
class ConfigProvider(Protocol):
    async def get(self, key: str) -> str | None:
        ...
```

**Contract:**
- Input: remote config key (e.g. `"woa.orchestration.db.type"`)
- Output: config value string, or `None` if not found
- Used for both non-sensitive configuration (Apollo, Nacos, Consul) and secrets (Vault, AWS Secrets Manager)
- `SecretResolver` is a backward-compatible alias for `ConfigProvider`
- `ConfigManager` accepts two `ConfigProvider` instances — one for config, one for secrets — differentiated by usage, not by type

---

## 3. Configuration System

### 3.1 ConfigSchema (import: `from mh_orchestration_service.config import ConfigSchema`)

```python
class ConfigSchema(BaseModel):
    token_secret_key: str                     # REQUIRED — JWT signing secret
    db_type: str                              # REQUIRED — "sqlite" | "opengauss"
    db_path: str                              # REQUIRED — SQLite file path
    db_host: str = ""                         # OpenGauss host
    db_port: int = 5432                       # Database port
    db_name: str = ""                         # Database name
    db_user: str = ""                         # Database user
    db_password: str = ""                     # Database password (sensitive)
    db_auto_schema: bool = False              # Auto-create tables
    cors_origins: list[str] = Field(default_factory=list)
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    enable_builtin_agents: bool = False      # Demo switch — OFF by default for production
    dev_mode: bool = False                  # Dev mode: exposes /api/v1/dev/* routes and SPA static files
```

**Constraint:** All Config classes (including custom ones) MUST inherit from `pydantic.BaseModel`. `ConfigManager.resolve()` accesses `model_fields` at runtime and calls the constructor with `**kwargs`.

**Resolution order** (per field, independent):
1. Environment variable `{PREFIX}_{FIELD}` (highest priority, e.g. `ORCH_LLM_API_KEY`)
2. `secret_resolver` (a `ConfigProvider` instance, if field in `sensitive_fields`)
3. `config_provider` (a `ConfigProvider` instance, if field NOT in `sensitive_fields`)
4. Model default value (optional fields only)
5. `ConfigError` raised (required fields only — those without defaults)

Both `config_provider` and `secret_resolver` use the same `ConfigProvider` protocol; `SecretResolver` is a backward-compatible alias.

### 3.2 ConfigMapping (import: `from mh_orchestration_service.config_mapping import ConfigMapping`)

```python
class ConfigMapping(BaseModel):
    key_mapping: dict[str, str] = {}     # field_name -> remote config key
    sensitive_keys: set[str] = set()     # field names treated as sensitive
```

### 3.3 ConfigManager (import: `from mh_orchestration_service.config_manager import ConfigManager`)

```python
class ConfigManager:
    def __init__(
        self,
        config_provider: ConfigProvider | None = None,
        secret_resolver: ConfigProvider | None = None,
    ) -> None: ...

    async def resolve(
        self,
        schema_cls: type[T],                    # BaseModel subclass
        *,
        prefix: str = "ORCH",
        sensitive_fields: set[str] | None = None,
        key_mapping: dict[str, str] | None = None,
    ) -> T: ...
```

Both parameters accept `ConfigProvider` instances. `SecretResolver` is a backward-compatible alias.

**Usage pattern for custom adapter config:**

```python
class MyAdapterConfig(BaseModel):
    api_url: str = "http://localhost:8080"
    api_key: str = ""

config_mgr = ConfigManager(
    config_provider=MyConfigProvider(),
    secret_resolver=MySecretResolver(),  # same protocol type, different instance
)
cfg = await config_mgr.resolve(
    MyAdapterConfig,
    prefix="my.adapter",
    sensitive_fields={"api_key"},
)
```

---

## 4. App Assembly (import: `from mh_orchestration_service.app import create_app`)

### 4.1 create_app() signature

```python
def create_app(
    *,
    settings: ConfigSchema,
    token_verifier: UserAuthProvider | None = None,
    permission_checker: PermissionChecker | None = None,
    credential_verifier: CredentialVerifier | None = None,
    registry_provider: RegistryProvider | None = None,
    logger: logging.Logger | None = None,
    llm_provider_factory: Callable[[], LLMProvider] | None = None,
) -> FastAPI:
```

**Parameter rules:**
- `settings` is REQUIRED
- `token_verifier` / `permission_checker`: if both None → shared `_DefaultAuthProvider`; if one is None → its own `_DefaultAuthProvider` instance
- `credential_verifier` None → `_DefaultCredentialVerifier` (hardcoded users)
- `registry_provider` None → `RegistryClient(enable_builtin=settings.enable_builtin_agents)` (empty when builtin disabled, demo data when enabled)
- `llm_provider_factory` None → `partial(OpenAILLMProvider, ...)` from env config via `AsyncOpenAI` client
- `logger` None → Python root logger

### 4.2 AppState (import: `from mh_orchestration_service.app import AppState`)

Accessible at runtime via `request.app.state.adapters`:

```python
class AppState:
    settings: ConfigSchema
    token_verifier: UserAuthProvider
    permission_checker: PermissionChecker
    credential_verifier: CredentialVerifier | None
    registry_provider: RegistryProvider | None
    logger: logging.Logger | None
    llm_provider_factory: Callable[[], LLMProvider] | None
```

---

## 5. Assembly Procedure (Code Agent MUST follow this order)

### Step 1: Resolve framework config

```python
config_mgr = ConfigManager(                     # no args = env-only mode
    config_provider=MyConfigProvider(),          # optional, ConfigProvider instance
    secret_resolver=MySecretResolver(),          # optional, also ConfigProvider
)
```

### Step 2: Resolve adapter-specific config (if needed)

```python
class MyRegistryConfig(BaseModel):
    api_url: str = ""
    api_key: str = ""

cfg = asyncio.run(config_mgr.resolve(
    MyRegistryConfig,
    prefix="my.registry",
    sensitive_fields={"api_key"},
))
```

### Step 3: Instantiate adapters with resolved config

```python
registry = MyRegistry(api_url=cfg.api_url, api_key=cfg.api_key)
```

### Step 4: Assemble app

```python
app = create_app(
    settings=settings,
    token_verifier=MyUserAuthProvider(),
    permission_checker=MyPermissionChecker(),
    credential_verifier=MyCredentialVerifier(),  # optional
    registry_provider=registry,                  # optional
    logger=logger,                               # optional
)
```

### Step 5: Expose `app` at module level (for uvicorn)

```python
# my_app.py — uvicorn my_app:app
```

---

## 6. Startup Behavior

- On module import, `asyncio.run()` resolves config
- `create_app()` sets up FastAPI with CORS, routers, middleware
- On server startup (lifespan): `init_db(settings.database_url, db_type=settings.db_type, auto_schema=settings.db_auto_schema)`
- On server shutdown: close auth providers, credential verifier, registry client, database connection
- **Required fields missing → `ConfigError` raised → process exits with error message listing missing keys**

---

## 7. Public API Surface (routes available after startup)

| Method | Path | Requires | Notes |
|--------|------|----------|-------|
| POST | `/api/v1/auth/login` | CredentialVerifier | 501 if not injected |
| GET | `/api/v1/auth/me` | Valid token | Returns UserIdentity |
| GET | `/api/v1/auth/sso/providers` | — | Returns configured SSO providers |
| POST | `/api/v1/chat/{memory_id}` | Valid token, session ownership | SSE streaming |
| GET | `/api/v1/sessions` | Valid token | User's sessions |
| GET | `/api/v1/sessions/{id}` | Valid token | Session detail + messages |
| DELETE | `/api/v1/sessions/{id}` | Valid token | Soft delete |
| GET | `/api/v1/scenarios` | Valid token | Permission-filtered |
| GET | `/api/v1/agents` | Valid token | Permission-filtered |
| GET | `/api/v1/tools` | Valid token | Permission-filtered |

---

## 8. Error Handling

| Error Type | Source | Behavior |
|---|---|---|
| `ConfigError` | `ConfigManager.resolve()` | Process exits with list of missing required fields |
| `RuntimeError` | `create_app()`/`get_db()` | Raised if `create_app()` not called before route handling |
| 401 | Auth middleware | Invalid/missing token |
| 403 | PermissionMiddleware | Missing required permission for tool/scenario |
| 501 | CredentialVerifier missing | POST /api/v1/auth/login |
| 404 | RegistryProvider | Agent/tool/scenario not found |
