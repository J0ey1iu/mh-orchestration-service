from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RegistryProvider(Protocol):
    """Registry metadata provider (agents + tools + scenarios).

    Customer deployment: implement this protocol to query your own
    registry system instead of the built-in registry-service.
    """

    async def get_agent(self, name: str) -> dict[str, Any] | None: ...
    async def list_agents(self) -> list[dict[str, Any]]: ...
    async def get_tool(self, name: str) -> dict[str, Any] | None: ...
    async def list_tools(self) -> list[dict[str, Any]]: ...
    async def get_scenario(self, scenario_id: str) -> dict[str, Any] | None: ...
    async def list_scenarios(self) -> list[dict]: ...


@runtime_checkable
class MetadataManager(RegistryProvider, Protocol):
    """Unified metadata provider for agents, tools, and scenarios.

    Combines read (from ``RegistryProvider``) and write operations so that
    customer deployments only need to implement a single protocol instead of
    two separate ones.

    All methods accept/return plain ``dict`` — the orchestration layer does
    not impose a fixed schema beyond the keys listed in the doc-string of
    each method.
    """

    # ── Tool CRUD ──

    async def create_tool(self, tool: dict[str, Any]) -> dict[str, Any]: ...
    async def update_tool(self, name: str, tool: dict[str, Any]) -> dict[str, Any]: ...
    async def delete_tool(self, name: str) -> None: ...

    # ── Agent CRUD ──

    async def create_agent(self, agent: dict[str, Any]) -> dict[str, Any]: ...
    async def update_agent(
        self, name: str, agent: dict[str, Any]
    ) -> dict[str, Any]: ...
    async def delete_agent(self, name: str) -> None: ...

    # ── Scenario CRUD ──

    async def create_scenario(self, scenario: dict[str, Any]) -> dict[str, Any]: ...
    async def update_scenario(
        self, scenario_id: str, scenario: dict[str, Any]
    ) -> dict[str, Any]: ...
    async def delete_scenario(self, scenario_id: str) -> None: ...

    # ── Scenario-Agent-Tool relationships ──

    async def add_scenario_agent(
        self, scenario_id: str, agent_name: str, tool_names: list[str] | None = None
    ) -> dict[str, Any]: ...
    async def remove_scenario_agent(
        self, scenario_id: str, agent_name: str
    ) -> dict[str, Any]: ...
    async def add_agent_tool(
        self, scenario_id: str, agent_name: str, tool_name: str
    ) -> dict[str, Any]: ...
    async def remove_agent_tool(
        self, scenario_id: str, agent_name: str, tool_name: str
    ) -> dict[str, Any]: ...

    async def close(self) -> None: ...


@runtime_checkable
class LLMProviderStore(Protocol):
    """CRUD for user-configured LLM provider credentials.

    Customer deployment: implement this protocol to store provider
    credentials (api_key, base_url) in your own secret store.
    All methods accept/return plain ``dict``.
    """

    async def list_providers(self) -> list[dict[str, Any]]: ...
    async def get_provider(self, name: str) -> dict[str, Any] | None: ...
    async def create_provider(self, provider: dict[str, Any]) -> dict[str, Any]: ...
    async def update_provider(
        self, name: str, provider: dict[str, Any]
    ) -> dict[str, Any]: ...
    async def delete_provider(self, name: str) -> None: ...
    async def close(self) -> None: ...
