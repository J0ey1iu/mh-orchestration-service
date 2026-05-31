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
        "admin": [
            "use:agent:*",
            "use:tool:*",
            "use:scene:*",
            "use:eval:*",
            "manage:scene:*",
            "manage:agent:*",
            "manage:tool:*",
        ],
        "member": [
            "use:agent:triage",
            "use:tool:calculator",
            "use:scene:triage",
            "manage:scene:*",
        ],
        "user": [
            "use:agent:code-reviewer",
            "use:agent:writer",
            "use:tool:web_search",
            "use:scene:code_review",
            "use:scene:writing",
        ],
        "scene-manager": [
            "manage:scene:*",
        ],
        "agent-manager": [
            "manage:agent:*",
        ],
        "tool-manager": [
            "manage:tool:*",
        ],
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
        return UserIdentity(user_id=uid, username=uid)

    async def get_permissions(self, user_id: str) -> list[str]:
        return self._permissions.get(user_id, [])

    async def check(self, user_id: str, permission: str) -> bool:
        perms = await self.get_permissions(user_id)
        from minimal_harness.auth.protocols import match_permission

        return match_permission(perms, permission)
