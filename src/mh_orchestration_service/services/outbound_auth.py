from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class OutboundAuthProvider(Protocol):
    """为出站远程 agent / tool 调用注入认证 header。

    客户企业部署时实现此 protocol，将当前请求的身份凭证传递给下游
    agent/tool 服务，例如转发 ``Authorization: Bearer <token>``、
    添加服务间 HMAC 签名等。

    此 protocol 在 ``runtime_service._tool_binding()`` 中调用，
    返回值会合并到 ``RemoteToolBinding.headers`` 中，
    最终由 ``SSEToolExecutor`` 设置在出站 HTTP 请求的 header 中。
    """

    async def get_headers(
        self,
        request: Any,
        target_url: str,
        target_type: str,
    ) -> dict[str, str]: ...
