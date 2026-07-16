from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mh_orchestration_service.adapters import ProviderStore


class InMemoryProviderStore(ProviderStore):
    """In-memory ``ProviderStore`` with seed data for out-of-box experience.

    Pre-seeded with two empty providers (no api_key):
    - ``openai`` — standard OpenAI (type: openai)
    - ``anthropic`` — standard Anthropic

    Users must configure credentials (api_key, base_url) via the
    management UI before these providers can be used at runtime.
    """

    def __init__(self, enable_builtin: bool = True) -> None:
        self._providers: dict[str, dict[str, Any]] = {}
        if enable_builtin:
            now = datetime.now(UTC).isoformat()
            self._providers["openai"] = {
                "name": "openai",
                "provider_type": "openai",
                "api_key": "",
                "base_url": "",
                "default_model": "",
                "description": "Standard OpenAI-compatible provider",
                "created_at": now,
                "updated_at": now,
            }
            self._providers["anthropic"] = {
                "name": "anthropic",
                "provider_type": "anthropic",
                "api_key": "",
                "base_url": "",
                "default_model": "",
                "description": "Standard Anthropic provider",
                "created_at": now,
                "updated_at": now,
            }

    async def list_providers(self) -> list[dict[str, Any]]:
        return [dict(p) for p in self._providers.values()]

    async def get_provider(self, name: str) -> dict[str, Any] | None:
        p = self._providers.get(name)
        return dict(p) if p else None

    async def create_provider(self, provider: dict[str, Any]) -> dict[str, Any]:
        name = provider.get("name", "")
        if not name:
            raise ValueError("Provider name is required")
        if name in self._providers:
            raise ValueError(f"Provider '{name}' already exists")
        now = datetime.now(UTC).isoformat()
        entry = {
            "name": name,
            "provider_type": provider.get("provider_type", "openai"),
            "api_key": provider.get("api_key", ""),
            "base_url": provider.get("base_url", ""),
            "default_model": provider.get("default_model", ""),
            "description": provider.get("description", ""),
            "created_by": provider.get("created_by", ""),
            "updated_by": provider.get("updated_by", ""),
            "created_at": now,
            "updated_at": now,
        }
        self._providers[name] = entry
        return dict(entry)

    async def update_provider(
        self, name: str, provider: dict[str, Any]
    ) -> dict[str, Any]:
        existing = self._providers.get(name)
        if existing is None:
            raise ValueError(f"Provider '{name}' not found")
        now = datetime.now(UTC).isoformat()
        merged = {**existing, **provider, "name": name, "updated_at": now}
        if "updated_by" in provider:
            merged["updated_by"] = provider["updated_by"]
        self._providers[name] = merged
        return dict(merged)

    async def delete_provider(self, name: str) -> None:
        if name not in self._providers:
            raise ValueError(f"Provider '{name}' not found")
        del self._providers[name]

    async def close(self) -> None:
        self._providers.clear()
