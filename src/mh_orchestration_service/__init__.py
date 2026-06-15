from mh_orchestration_service.adapters import MetadataManager, RegistryProvider
from mh_orchestration_service.app import AppState, LifespanHook, create_app
from mh_orchestration_service.auth import (
    PermissionChecker,
    UserAuthProvider,
    UserIdentity,
    match_permission,
)
from mh_orchestration_service.config import ConfigSchema
from mh_orchestration_service.config_manager import ConfigError, ConfigManager
from mh_orchestration_service.config_protocols import ConfigProvider, SecretResolver
from mh_orchestration_service.context import (
    get_current_auth_token,
    get_current_cookies,
    get_current_locale,
    get_current_request,
    get_current_trace_id,
    get_current_user_id,
)
from mh_orchestration_service.services.auth_client import _DefaultAuthProvider
from mh_orchestration_service.services.generated_agent_provider import AgentGenerator
from mh_orchestration_service.services.generated_tool_provider import ToolGenerator
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

DefaultAuthProvider = _DefaultAuthProvider
DefaultM2MAuthProvider = _DefaultM2MAuthProvider
DefaultOutboundAuthProvider = _DefaultOutboundAuthProvider

__all__ = (
    "AgentGenerator",
    "AppState",
    "ConfigError",
    "ConfigManager",
    "ConfigProvider",
    "ConfigSchema",
    "DefaultAuthProvider",
    "DefaultM2MAuthProvider",
    "DefaultOutboundAuthProvider",
    "InMemoryManagementProvider",
    "LifespanHook",
    "M2MAuthProvider",
    "MetadataManager",
    "OutboundAuthProvider",
    "PermissionChecker",
    "RegistryProvider",
    "SecretResolver",
    "ToolGenerator",
    "UserAuthProvider",
    "UserIdentity",
    "_DefaultM2MAuthProvider",
    "_DefaultOutboundAuthProvider",
    "create_app",
    "get_current_auth_token",
    "get_current_cookies",
    "get_current_locale",
    "get_current_request",
    "get_current_trace_id",
    "get_current_user_id",
    "match_permission",
)
