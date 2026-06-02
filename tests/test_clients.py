from unittest.mock import MagicMock

import pytest
from mh_orchestration_service.services.auth_client import _DefaultAuthProvider
from mh_orchestration_service.services.registry_client import RegistryClient


def _mock_request(x_user_id: str = "") -> MagicMock:
    r = MagicMock()
    r.headers.get.return_value = x_user_id
    r.cookies.get.return_value = None
    return r


class Test_DefaultAuthProvider:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.client = _DefaultAuthProvider()

    @pytest.mark.asyncio
    async def test_verify_x_user_id_header(self):
        request = _mock_request(x_user_id="1")
        identity = await self.client.verify(request)
        assert identity is not None
        assert identity.user_id == "1"
        assert identity.username == "Admin"
        assert identity.roles == ["admin"]

    @pytest.mark.asyncio
    async def test_verify_missing_header(self):
        request = _mock_request(x_user_id="")
        identity = await self.client.verify(request)
        assert identity is None

    @pytest.mark.asyncio
    async def test_verify_x_user_id_cookie(self):
        request = _mock_request(x_user_id="")
        request.cookies.get.side_effect = lambda key: (
            "1" if key == "x-user-id" else None
        )
        identity = await self.client.verify(request)
        assert identity is not None
        assert identity.user_id == "1"

    @pytest.mark.asyncio
    async def test_get_permissions_admin(self):
        perms = await self.client.get_permissions("1")
        assert "use:agent:*" in perms

    @pytest.mark.asyncio
    async def test_get_permissions_unknown(self):
        perms = await self.client.get_permissions("unknown_user")
        assert perms == []

    @pytest.mark.asyncio
    async def test_check_permission_exact_match(self):
        self.client._permissions = {"1": ["use:agent:code-reviewer"]}
        result = await self.client.check("1", "use:agent:code-reviewer")
        assert result is True

    @pytest.mark.asyncio
    async def test_check_permission_wildcard_resource(self):
        self.client._permissions = {"1": ["use:agent:*"]}
        result = await self.client.check("1", "use:agent:any-agent")
        assert result is True

    @pytest.mark.asyncio
    async def test_check_permission_wildcard_action(self):
        self.client._permissions = {"1": ["*:agent:code-reviewer"]}
        result = await self.client.check("1", "use:agent:code-reviewer")
        assert result is True

    @pytest.mark.asyncio
    async def test_check_permission_denied(self):
        self.client._permissions = {"1": ["use:agent:writer"]}
        result = await self.client.check("1", "use:agent:anything")
        assert result is False

    @pytest.mark.asyncio
    async def test_check_permission_empty(self):
        self.client._permissions = {"1": []}
        result = await self.client.check("1", "use:agent:anything")
        assert result is False

    @pytest.mark.asyncio
    async def test_scene_manager_has_only_scene_perms(self):
        self.client._permissions = dict(self.client.DEFAULT_PERMISSIONS)
        assert await self.client.check("4", "manage:scene:*") is True
        assert await self.client.check("4", "manage:agent:*") is False
        assert await self.client.check("4", "manage:tool:*") is False
        assert await self.client.check("4", "use:scene:*") is False

    @pytest.mark.asyncio
    async def test_agent_manager_has_only_agent_perms(self):
        self.client._permissions = dict(self.client.DEFAULT_PERMISSIONS)
        assert await self.client.check("5", "manage:agent:*") is True
        assert await self.client.check("5", "manage:scene:*") is False
        assert await self.client.check("5", "manage:tool:*") is False

    @pytest.mark.asyncio
    async def test_tool_manager_has_only_tool_perms(self):
        self.client._permissions = dict(self.client.DEFAULT_PERMISSIONS)
        assert await self.client.check("6", "manage:tool:*") is True
        assert await self.client.check("6", "manage:scene:*") is False
        assert await self.client.check("6", "manage:agent:*") is False


class TestRegistryClient:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.client = RegistryClient(enable_builtin=True)

    @pytest.mark.asyncio
    async def test_get_agent_found(self):
        agent = await self.client.get_agent("code-reviewer")
        assert agent is not None
        assert agent["name"] == "code-reviewer"

    @pytest.mark.asyncio
    async def test_get_agent_not_found(self):
        agent = await self.client.get_agent("nonexistent")
        assert agent is None

    @pytest.mark.asyncio
    async def test_get_tool_found(self):
        tool = await self.client.get_tool("web_search")
        assert tool is not None
        assert tool["name"] == "web_search"

    @pytest.mark.asyncio
    async def test_get_tool_not_found(self):
        tool = await self.client.get_tool("nonexistent")
        assert tool is None

    @pytest.mark.asyncio
    async def test_list_agents(self):
        agents = await self.client.list_agents()
        assert len(agents) == 3

    @pytest.mark.asyncio
    async def test_list_tools(self):
        tools = await self.client.list_tools()
        assert len(tools) == 6

    @pytest.mark.asyncio
    async def test_custom_data(self):
        client = RegistryClient(
            agents=[{"name": "custom-agent"}],
            tools=[{"name": "custom-tool"}],
            scenarios=[],
        )
        assert len(await client.list_agents()) == 1
        assert len(await client.list_tools()) == 1
        assert await client.get_agent("custom-agent") is not None
