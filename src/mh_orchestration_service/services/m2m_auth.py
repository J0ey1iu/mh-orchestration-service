from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class M2MAuthProvider(Protocol):
    """机机接口鉴权提供者。

    在 agent 调用端点（``POST /api/v1/agents/{name}/run``）和
    runtime tool 端点（``POST /api/v1/tools/*/execute``）被调用时
    验证调用方身份。

    客户企业部署时实现此 protocol，通过 SOA 或其他机制验证调用方
    应用身份。接收原始 ``Request`` 对象，可自行决定检查方式（header、
    cookie、mTLS 证书等）。

    返回 ``app_id`` 表示鉴权通过（拥有所有权限），返回 ``None`` 表示失败。
    """

    async def authenticate(self, request: Any) -> str | None:
        """验证机机调用方身份。

        Args:
            request: 当前 FastAPI Request 对象。

        Returns:
            app_id 表示鉴权通过，``None`` 表示鉴权失败（调用方返回 401）。
        """
        ...

    async def get_identity_headers(self, request: Any, identity: str) -> dict[str, str]:
        """返回注入到出站 RemoteToolBinding 的身份 header。

        当 chat 流程调用下游 remote tool 时，此方法返回的 header
        会被写入 HTTP 请求，让下游 M2M 鉴权端点识别调用方身份。

        Args:
            request: 当前 FastAPI Request 对象。
            identity: 调用方身份标识（user_id 或 app_id）。

        Returns:
            Header 键值对字典，会写入出站 binding 的 ``headers`` 字段。
            默认返回 ``{}``。
        """
        ...

    async def close(self) -> None:
        """释放资源。"""
        ...


class _DefaultM2MAuthProvider:
    """默认实现——开发者样例，仅 log 请求信息，不做任何鉴权控制。

    生产环境必须替换为实际的 M2M 鉴权实现（如 SOA）。
    """

    async def close(self) -> None:
        pass

    async def authenticate(self, request: Any) -> str | None:
        logger.info(
            "M2M authenticate: method=%s url=%s headers=%s client=%s",
            request.method,
            str(request.url),
            dict(request.headers),
            request.client.host if request.client else None,
        )
        return "default"

    async def get_identity_headers(self, request: Any, identity: str) -> dict[str, str]:
        logger.info(
            "M2M get_identity_headers: identity=%s method=%s url=%s headers=%s client=%s",
            identity,
            request.method,
            str(request.url),
            dict(request.headers),
            request.client.host if request.client else None,
        )
        return {}
