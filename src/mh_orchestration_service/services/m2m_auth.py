from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


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

    async def authenticate(self, request: Any) -> str | None: ...

    async def get_identity_headers(
        self, request: Any, identity: str
    ) -> dict[str, str]: ...

    async def close(self) -> None: ...
