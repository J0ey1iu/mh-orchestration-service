from __future__ import annotations

from typing import Any

from minimal_harness.auth import PermissionChecker, UserAuthProvider, UserIdentity


class _DefaultAuthProvider(UserAuthProvider, PermissionChecker):
    """内置默认认证提供者——纯开发用。

    从 ``X-User-Id`` 请求头或 ``x-user-id`` cookie 直接读取用户标识，
    不做任何签名校验。客户生产环境应替换为 ``UserAuthProvider`` 的
    自定义实现，对接企业 SSO 完成 token 验证。
    """

    DEFAULT_PERMISSIONS: dict[str, list[str]] = {
        "default": [
            "use:agent:*",
            "use:tool:*",
            "use:scene:*",
            "use:eval:*",
            "manage:scene:*",
            "manage:agent:*",
            "manage:tool:*",
        ],
        "1": [
            "use:agent:*",
            "use:tool:*",
            "use:scene:*",
            "use:eval:*",
            "manage:scene:*",
            "manage:agent:*",
            "manage:tool:*",
        ],
        "2": [
            "use:agent:triage",
            "use:tool:calculator",
            "use:scene:triage",
            "manage:scene:*",
        ],
        "3": [
            "use:agent:code-reviewer",
            "use:agent:writer",
            "use:tool:web_search",
            "use:scene:code_review",
            "use:scene:writing",
        ],
        "4": [
            "manage:scene:*",
        ],
        "5": [
            "manage:agent:*",
        ],
        "6": [
            "manage:tool:*",
        ],
    }

    USER_NAMES: dict[str, str] = {
        "1": "Admin",
        "2": "Member",
        "3": "User",
        "4": "Scene Manager",
        "5": "Agent Manager",
        "6": "Tool Manager",
    }

    ROLE_NAMES: dict[str, str] = {
        "1": "admin",
        "2": "member",
        "3": "user",
        "4": "scene-manager",
        "5": "agent-manager",
        "6": "tool-manager",
    }

    def __init__(
        self,
        permissions: dict[str, list[str]] | None = None,
    ) -> None:
        self._permissions = (
            dict(permissions)
            if permissions is not None
            else dict(self.DEFAULT_PERMISSIONS)
        )

    async def close(self) -> None:
        pass

    async def verify(self, request: Any) -> UserIdentity | None:
        uid = request.headers.get("X-User-Id") or request.cookies.get("x-user-id")
        if not uid:
            return None
        return UserIdentity(
            user_id=uid,
            username=self.USER_NAMES.get(uid, uid),
            roles=[self.ROLE_NAMES.get(uid, uid)],
        )

    async def get_permissions(self, user_id: str) -> list[str]:
        return self._permissions.get(user_id, [])

    async def logout(self, request: Any, response: Any) -> None:
        response.set_cookie(
            key="x-user-id", value="", httponly=True, max_age=0, path="/"
        )

    async def check(self, user_id: str, permission: str) -> bool:
        perms = await self.get_permissions(user_id)
        from minimal_harness.auth.protocols import match_permission

        return match_permission(perms, permission)
