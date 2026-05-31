from __future__ import annotations

from typing import Any

from minimal_harness.agent.middleware import Middleware
from minimal_harness.auth import PermissionChecker
from minimal_harness.types import ToolCall


class PermissionMiddleware(Middleware):
    def __init__(self, user_id: str, permission_checker: PermissionChecker) -> None:
        self._user_id = user_id
        self._permission_checker = permission_checker

    async def should_allow_tool(
        self, tool_call: ToolCall, *args: Any, **kwargs: Any
    ) -> bool | str:
        tool_name = tool_call["function"]["name"]
        required_perm = f"use:tool:{tool_name}"
        allowed = await self._permission_checker.check(self._user_id, required_perm)
        if not allowed:
            return f"Permission denied: missing {required_perm}"
        return True
