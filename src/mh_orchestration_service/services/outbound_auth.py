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
    ) -> dict[str, str]:
        """返回要注入到出站请求中的 header 字典。

        Args:
            request: 当前 FastAPI Request 对象。
            target_url: 目标 agent / tool 的 endpoint URL。
            target_type: ``"agent"`` 或 ``"tool"``。

        Returns:
            Header 键值对字典，会合并到出站 HTTP 请求的 header 中。
        """
        ...


_HOP_BY_HOP_HEADERS = frozenset(
    {
        "host",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-length",
        "content-encoding",
    }
)


class _DefaultOutboundAuthProvider:
    """默认实现——将当前请求的原始 header 透传给下游 agent/tool 服务。

    剔除了 hop-by-hop header（host、content-length 等），其余全部转发，
    这样下游服务可以自行提取 Authorization、Cookie、X-User-Id 等任何
    它需要的字段，框架不需要预先猜测开发者需要什么。
    """

    async def close(self) -> None:
        pass

    async def get_headers(
        self,
        request: Any,
        target_url: str,
        target_type: str,
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        for key, value in request.headers.items():
            if key.lower() not in _HOP_BY_HOP_HEADERS:
                headers[key] = value
        return headers
