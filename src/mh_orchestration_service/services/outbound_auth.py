from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class OutboundAuthProvider(Protocol):
    """为出站远程 agent / tool 调用注入认证 header。

    客户企业部署时实现此 protocol，将当前请求的身份凭证传递给下游
    agent/tool 服务，例如转发 ``Authorization: Bearer <token>``、
    添加服务间 HMAC 签名等。

    此 protocol 在 ``runtime_service._agent_binding()`` 和
    ``_tool_binding()`` 中调用，返回值会合并到
    ``RemoteAgentBinding.headers`` / ``RemoteToolBinding.headers`` 中，
    最终由 ``SSEAgentDriver`` / ``SSEToolExecutor`` 设置在出站 HTTP
    请求的 header 中。
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

    .. warning::

        This default forwards the inbound ``Authorization``, ``Cookie``,
        and any identity-bearing header to whatever URL is set as
        ``endpoint_url`` on a remote agent / tool.  A privileged user
        who can set ``endpoint_url`` to an attacker-controlled host
        can use this to exfiltrate session credentials.  **Customer
        production deployments MUST override this provider** (or run
        with a hardened firewall around the management API).  See
        ``docs/customer-adaptation-guide.md`` for the threat model.
    """

    async def close(self) -> None:
        pass

    async def get_headers(
        self,
        request: Any,
        target_url: str,
        target_type: str,
    ) -> dict[str, str]:
        # Per-call WARNING: keep the risk visible in production logs so
        # operators can confirm (a) the default provider is in use and
        # (b) every outbound call ships the inbound credentials.
        logger.warning(
            "outbound_auth.default.in_use target=%s type=%s — default "
            "provider forwards all inbound headers (incl. Authorization, "
            "Cookie) to the configured endpoint_url. Override this "
            "provider in production.",
            target_url,
            target_type,
        )
        headers: dict[str, str] = {}
        for key, value in request.headers.items():
            if key.lower() not in _HOP_BY_HOP_HEADERS:
                headers[key] = value
        return headers
