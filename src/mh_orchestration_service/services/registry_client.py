from __future__ import annotations

from typing import Any, AsyncIterator, Callable

from mh_orchestration_service.adapters import RegistryProvider
from mh_orchestration_service.builtin_agents import (
    BUILTIN_AGENTS,
    BUILTIN_SCENARIOS,
    BUILTIN_TOOLS,
)


class RegistryClient(RegistryProvider):
    def __init__(
        self,
        agents: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
        scenarios: list[dict[str, Any]] | None = None,
        *,
        extra_agents: list[dict[str, Any]] | None = None,
        extra_tools: list[dict[str, Any]] | None = None,
        extra_scenarios: list[dict[str, Any]] | None = None,
        enable_builtin: bool = False,
    ) -> None:
        if agents is not None:
            self._agents = list(agents)
        elif enable_builtin:
            self._agents = list(BUILTIN_AGENTS) + (extra_agents or [])
        else:
            self._agents = list(extra_agents or [])
        if tools is not None:
            self._tools = list(tools)
        elif enable_builtin:
            self._tools = list(BUILTIN_TOOLS) + (extra_tools or [])
        else:
            self._tools = list(extra_tools or [])
        if scenarios is not None:
            self._scenarios = list(scenarios)
        elif enable_builtin:
            self._scenarios = list(BUILTIN_SCENARIOS) + (extra_scenarios or [])
        else:
            self._scenarios = list(extra_scenarios or [])
        self._tool_fns: dict[str, Callable[..., AsyncIterator[Any]]] = {}
        for t in self._tools:
            fn = t.get("_fn")
            if fn:
                self._tool_fns[t["name"]] = fn

    async def close(self) -> None:
        pass

    def get_tool_fn(self, name: str) -> Callable[..., AsyncIterator[Any]] | None:
        return self._tool_fns.get(name)

    async def get_agent(self, name: str) -> dict[str, Any] | None:
        for a in self._agents:
            if a.get("name") == name:
                return a
        return None

    async def list_agents(self) -> list[dict[str, Any]]:
        return list(self._agents)

    async def get_tool(self, name: str) -> dict[str, Any] | None:
        for t in self._tools:
            if t.get("name") == name:
                return t
        return None

    async def list_tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    async def get_scenario(self, scenario_id: str) -> dict[str, Any] | None:
        for s in self._scenarios:
            if s.get("id") == scenario_id:
                return s
        return None

    async def list_scenarios(self) -> list[dict[str, Any]]:
        return list(self._scenarios)
