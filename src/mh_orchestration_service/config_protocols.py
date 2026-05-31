from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ConfigProvider(Protocol):
    """External configuration / secret provider.

    Customer deployment: implement this protocol to load configuration
    and/or secrets from your own config center / vault (e.g. Apollo,
    Nacos, Consul, HashiCorp Vault, AWS Secrets Manager) instead of
    environment variables.

    ``SecretResolver`` is a backward-compatible alias.
    """

    async def get(self, key: str) -> str | None:
        """Return the config/secret value for *key*, or None if not found."""
        ...


SecretResolver = ConfigProvider
