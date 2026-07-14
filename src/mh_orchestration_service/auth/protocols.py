from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from mh_orchestration_service.local.permissions import match_permission


@dataclass
class UserIdentity:
    """Standard identity object returned by token verification."""

    user_id: str
    username: str = ""
    roles: list[str] = field(default_factory=list)
    extra_data: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class UserAuthProvider(Protocol):
    """Validates authentication requests and resolves them to a UserIdentity.

    Customer deployment: implement this protocol to integrate with
    the customer's SSO / token introspection endpoint.

    The ``request`` argument is the raw HTTP request (e.g. FastAPI ``Request``).
    Implementations may read headers, cookies, query parameters, or call
    external auth services to determine the caller's identity.
    """

    async def verify(self, request: Any) -> UserIdentity | None:
        """Validate *request* and return the user identity, or None if invalid."""
        ...

    async def logout(self, request: Any, response: Any) -> None:
        """Clear authentication state on explicit logout.

        Called when a user explicitly logs out. Implementations **must** clear
        any cookies, tokens, or session state on the *response* that was used
        to authenticate the *request*.
        """
        ...


@runtime_checkable
class PermissionChecker(Protocol):
    """Checks permissions for a given user.

    Customer deployment: implement this protocol to integrate with
    the customer's permission system (RBAC, OPA, OpenFGA, etc.).
    """

    async def get_permissions(self, user_id: str) -> list[str]:
        """Return all permission strings for the given user."""
        ...

    async def check(self, user_id: str, permission: str) -> bool:
        """Check whether *user_id* has a specific *permission*.

        Permission strings follow the format ``action:resource:target``
        and support ``*`` wildcards at any segment.
        """
        ...


__all__ = [
    "PermissionChecker",
    "UserAuthProvider",
    "UserIdentity",
    "match_permission",
]
