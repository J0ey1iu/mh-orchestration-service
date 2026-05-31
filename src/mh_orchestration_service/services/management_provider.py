from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable


from mh_orchestration_service.services.registry_client import RegistryClient


@runtime_checkable
class ManagementProvider(Protocol):
    """Deprecated: use ``MetadataManager`` instead.

    This protocol is kept for backward compatibility. New customer
    deployments should implement ``MetadataManager`` which combines
    read (``RegistryProvider``) and write operations.
    """

    async def create_scenario(self, scenario: dict[str, Any]) -> dict[str, Any]: ...
    async def update_scenario(
        self, scenario_id: str, scenario: dict[str, Any]
    ) -> dict[str, Any]: ...
    async def delete_scenario(self, scenario_id: str) -> None: ...

    async def create_agent(self, agent: dict[str, Any]) -> dict[str, Any]: ...
    async def update_agent(
        self, name: str, agent: dict[str, Any]
    ) -> dict[str, Any]: ...
    async def delete_agent(self, name: str) -> None: ...

    async def create_tool(self, tool: dict[str, Any]) -> dict[str, Any]: ...
    async def update_tool(self, name: str, tool: dict[str, Any]) -> dict[str, Any]: ...
    async def delete_tool(self, name: str) -> None: ...

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


class InMemoryManagementProvider(RegistryClient):
    """In-memory implementation of ``MetadataManager`` for dev/testing.

    Extends ``RegistryClient`` so creates/updates/deletes are immediately
    visible through the read-only ``RegistryProvider`` interface.
    """

    async def create_scenario(self, scenario: dict[str, Any]) -> dict[str, Any]:
        sid = scenario.get("id", "")
        if any(s.get("id") == sid for s in self._scenarios):
            raise ValueError(f"Scenario '{sid}' already exists")
        now = datetime.now(UTC).isoformat()
        entry = {**scenario, "created_at": now, "updated_at": now}
        self._scenarios.append(entry)
        return dict(entry)

    async def update_scenario(
        self, scenario_id: str, scenario: dict[str, Any]
    ) -> dict[str, Any]:
        for i, s in enumerate(self._scenarios):
            if s.get("id") == scenario_id:
                now = datetime.now(UTC).isoformat()
                merged = {**s, **scenario, "id": scenario_id, "updated_at": now}
                self._scenarios[i] = merged
                return dict(merged)
        raise ValueError(f"Scenario '{scenario_id}' not found")

    async def delete_scenario(self, scenario_id: str) -> None:
        for i, s in enumerate(self._scenarios):
            if s.get("id") == scenario_id:
                self._scenarios.pop(i)
                return
        raise ValueError(f"Scenario '{scenario_id}' not found")

    async def create_agent(self, agent: dict[str, Any]) -> dict[str, Any]:
        name = agent.get("name", "")
        if any(a.get("name") == name for a in self._agents):
            raise ValueError(f"Agent '{name}' already exists")
        now = datetime.now(UTC).isoformat()
        entry = {**agent, "created_at": now, "updated_at": now}
        self._agents.append(entry)
        return dict(entry)

    async def update_agent(self, name: str, agent: dict[str, Any]) -> dict[str, Any]:
        for i, a in enumerate(self._agents):
            if a.get("name") == name:
                now = datetime.now(UTC).isoformat()
                merged = {**a, **agent, "name": name, "updated_at": now}
                self._agents[i] = merged
                return dict(merged)
        raise ValueError(f"Agent '{name}' not found")

    async def delete_agent(self, name: str) -> None:
        for i, a in enumerate(self._agents):
            if a.get("name") == name:
                self._agents.pop(i)
                return
        raise ValueError(f"Agent '{name}' not found")

    async def create_tool(self, tool: dict[str, Any]) -> dict[str, Any]:
        tname = tool.get("name", "")
        if any(t.get("name") == tname for t in self._tools):
            raise ValueError(f"Tool '{tname}' already exists")
        now = datetime.now(UTC).isoformat()
        entry = {**tool, "created_at": now, "updated_at": now}
        if "_fn" in entry:
            self._tool_fns[tname] = entry["_fn"]
        self._tools.append(entry)
        return dict(entry)

    async def update_tool(self, name: str, tool: dict[str, Any]) -> dict[str, Any]:
        for i, t in enumerate(self._tools):
            if t.get("name") == name:
                now = datetime.now(UTC).isoformat()
                merged = {**t, **tool, "name": name, "updated_at": now}
                self._tools[i] = merged
                if "_fn" in merged:
                    self._tool_fns[name] = merged["_fn"]
                return dict(merged)
        raise ValueError(f"Tool '{name}' not found")

    async def delete_tool(self, name: str) -> None:
        for i, t in enumerate(self._tools):
            if t.get("name") == name:
                self._tools.pop(i)
                self._tool_fns.pop(name, None)
                return
        raise ValueError(f"Tool '{name}' not found")

    async def add_scenario_agent(
        self, scenario_id: str, agent_name: str, tool_names: list[str] | None = None
    ) -> dict[str, Any]:
        for s in self._scenarios:
            if s.get("id") == scenario_id:
                agents = s.get("agents", [])
                if any(a.get("name") == agent_name for a in agents):
                    raise ValueError(
                        f"Agent '{agent_name}' already in scenario '{scenario_id}'"
                    )
                agents.append({"name": agent_name, "tool_names": tool_names or []})
                s["agents"] = agents
                s["updated_at"] = datetime.now(UTC).isoformat()
                return dict(s)
        raise ValueError(f"Scenario '{scenario_id}' not found")

    async def remove_scenario_agent(
        self, scenario_id: str, agent_name: str
    ) -> dict[str, Any]:
        for s in self._scenarios:
            if s.get("id") == scenario_id:
                agents = s.get("agents", [])
                new_agents = [a for a in agents if a.get("name") != agent_name]
                if len(new_agents) == len(agents):
                    raise ValueError(
                        f"Agent '{agent_name}' not found in scenario '{scenario_id}'"
                    )
                s["agents"] = new_agents
                s["updated_at"] = datetime.now(UTC).isoformat()
                return dict(s)
        raise ValueError(f"Scenario '{scenario_id}' not found")

    async def add_agent_tool(
        self, scenario_id: str, agent_name: str, tool_name: str
    ) -> dict[str, Any]:
        for s in self._scenarios:
            if s.get("id") == scenario_id:
                agents = s.get("agents", [])
                for a in agents:
                    if a.get("name") == agent_name:
                        tools = a.get("tool_names", [])
                        if tool_name in tools:
                            raise ValueError(
                                f"Tool '{tool_name}' already assigned to "
                                f"agent '{agent_name}' in scenario '{scenario_id}'"
                            )
                        tools.append(tool_name)
                        a["tool_names"] = tools
                        s["updated_at"] = datetime.now(UTC).isoformat()
                        return dict(s)
                raise ValueError(
                    f"Agent '{agent_name}' not found in scenario '{scenario_id}'"
                )
        raise ValueError(f"Scenario '{scenario_id}' not found")

    async def remove_agent_tool(
        self, scenario_id: str, agent_name: str, tool_name: str
    ) -> dict[str, Any]:
        for s in self._scenarios:
            if s.get("id") == scenario_id:
                agents = s.get("agents", [])
                for a in agents:
                    if a.get("name") == agent_name:
                        tools = a.get("tool_names", [])
                        if tool_name not in tools:
                            raise ValueError(
                                f"Tool '{tool_name}' not assigned to "
                                f"agent '{agent_name}' in scenario '{scenario_id}'"
                            )
                        tools.remove(tool_name)
                        a["tool_names"] = tools
                        s["updated_at"] = datetime.now(UTC).isoformat()
                        return dict(s)
                raise ValueError(
                    f"Agent '{agent_name}' not found in scenario '{scenario_id}'"
                )
        raise ValueError(f"Scenario '{scenario_id}' not found")
