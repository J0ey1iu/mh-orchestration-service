from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass
class GeneratedAgentMeta:
    name: str
    display_name: str
    description: str
    system_prompt: str
    provider: str = "openai"
    model: str = ""
    llm_config: Optional[dict[str, Any]] = None
    user_id: str = ""
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        now = datetime.now(UTC).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now
        if self.llm_config is None:
            self.llm_config = {}


@runtime_checkable
class AgentGenerator(Protocol):
    """Pure agent generation — no storage.

    Generates agent metadata + system prompt from a natural language
    description. Persistence is handled by the caller via
    ``MetadataManager``.
    """

    def generate_stream(
        self, natural_description: str, stop_event: asyncio.Event | None = None
    ) -> AsyncGenerator[dict[str, Any], None]: ...


def agent_to_dict(a: GeneratedAgentMeta) -> dict[str, Any]:
    return {
        "name": a.name,
        "display_name": a.display_name,
        "description": a.description,
        "system_prompt": a.system_prompt,
        "provider": a.provider,
        "model": a.model,
        "llm_config": a.llm_config,
        "user_id": a.user_id,
        "created_at": a.created_at,
        "updated_at": a.updated_at,
    }
