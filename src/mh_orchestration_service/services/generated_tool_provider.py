from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable


@dataclass
class GeneratedToolMeta:
    name: str
    display_name: str
    description: str
    parameters: dict[str, Any]
    source_code: str
    user_id: str = ""
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        now = datetime.now(UTC).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now


@runtime_checkable
class ToolGenerator(Protocol):
    """Pure tool generation — no storage.

    Generates tool metadata + source code from a natural language
    description. Persistence is handled by the caller via
    ``MetadataManager``.
    """

    def generate_stream(
        self, natural_description: str, stop_event: asyncio.Event | None = None
    ) -> AsyncGenerator[dict[str, Any], None]: ...


def tool_to_dict(t: GeneratedToolMeta) -> dict[str, Any]:
    return {
        "name": t.name,
        "display_name": t.display_name,
        "description": t.description,
        "parameters": t.parameters,
        "source_code": t.source_code,
        "user_id": t.user_id,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }
