from collections.abc import Generator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mh_orchestration_service.app import create_app
from mh_orchestration_service.config import ConfigSchema
from minimal_harness.auth import UserIdentity

TEST_SCENARIOS = [
    {
        "id": "code_review",
        "name": "Code Review",
        "name_locale": "{}",
        "icon": "\U0001f4bb",
        "description": "Review code changes",
        "description_locale": "{}",
        "agents": [
            {
                "name": "code-reviewer",
                "tool_names": [],
            }
        ],
    },
    {
        "id": "writing",
        "name": "Writing Assistant",
        "name_locale": "{}",
        "icon": "\U0001f4dd",
        "description": "Help with writing",
        "description_locale": "{}",
        "agents": [
            {
                "name": "writer",
                "tool_names": ["web_search"],
            }
        ],
    },
]


ALL_PERMS = [
    "use:agent:*",
    "use:tool:*",
    "use:scene:*",
    "use:eval:*",
    "manage:scene:*",
    "manage:agent:*",
    "manage:tool:*",
]


@pytest.fixture
def test_app(tmp_path):
    settings = ConfigSchema(
        db_type="sqlite",
        db_path=str(tmp_path / "test.db"),
        cors_origins=[],
    )

    @asynccontextmanager
    async def mock_adapters_hook(app: FastAPI):
        """Replace default adapters with mocks inside lifespan."""
        adapters = app.state.adapters
        adapters.permission_checker.get_permissions = AsyncMock(return_value=ALL_PERMS)
        adapters.permission_checker.check = AsyncMock(
            side_effect=lambda uid, perm: True
        )
        adapters.token_verifier.verify = AsyncMock(
            return_value=UserIdentity(user_id="1", username="admin")
        )
        adapters.management_provider.list_scenarios = AsyncMock(
            return_value=TEST_SCENARIOS
        )
        adapters.management_provider.list_agents = AsyncMock(return_value=[])
        adapters.management_provider.list_tools = AsyncMock(return_value=[])
        yield

    return create_app(
        settings=settings,
        lifespan_hooks=[mock_adapters_hook],
    )


@pytest.fixture
def client(test_app) -> Generator[TestClient, None, None]:
    with TestClient(test_app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def auth_header() -> dict[str, str]:
    return {"X-User-Id": "1"}
