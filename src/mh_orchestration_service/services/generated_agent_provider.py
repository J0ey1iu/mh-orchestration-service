from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from minimal_harness.llm.llm import LLMProvider
from minimal_harness.memory import system_message, user_message

logger = logging.getLogger("orchestration.generated_agent_provider")


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


_AGENT_GENERATION_SYSTEM_PROMPT = """\
You are an AI agent designer. Given a user's description, output a JSON object with the agent's metadata.

Output format:
{
  "name": "snake_case_unique_identifier",
  "display_name": "Short Human-Readable Name",
  "description": "Clear description of what this agent does",
  "system_prompt": "The full system prompt that defines the agent's behavior, personality, constraints and capabilities",
  "provider": "openai",
  "model": "gpt-4o or deepseek-chat or similar",
  "llm_config": { "temperature": 0.7, "max_tokens": 4096 }
}

CRITICAL rules for system_prompt:
1. Write a complete, well-structured system prompt that clearly defines the agent's role.
2. Include personality traits, constraints, output format preferences, and any special behaviors.
3. The system prompt should be in the same language as the user's description.
4. Keep the system prompt concise but comprehensive — typically 200-800 characters.

Guidelines for other fields:
- name: short snake_case identifier, e.g. "code_reviewer", "data_analyst"
- display_name: short human-readable title in the user's language
- description: one or two sentences explaining the agent's purpose
- provider: always "openai" unless the user specifies otherwise
- model: suggest an appropriate model based on the task complexity
- llm_config: suggest reasonable defaults for temperature and max_tokens

Example output:
{
  "name": "data_analyst",
  "display_name": "Data Analyst",
  "description": "Expert data analyst that helps users explore, visualize, and derive insights from their data",
  "system_prompt": "You are a senior data analyst. Your role is to help users understand their data through analysis, visualization, and clear explanations. Always ask clarifying questions when the request is ambiguous. Present findings in a structured format with key takeaways first.",
  "provider": "openai",
  "model": "gpt-4o",
  "llm_config": { "temperature": 0.3, "max_tokens": 4096 }
}
"""


class DefaultAgentGenerator:
    """Pure LLM-based agent generator — no storage.

    Generates ``GeneratedAgentMeta`` from a natural-language description.
    The caller is responsible for persisting via ``MetadataManager``.
    """

    def __init__(
        self,
        llm_factory: Callable[[], LLMProvider] | None = None,
    ) -> None:
        self._llm_factory = llm_factory

    def set_llm_factory(self, llm_factory: Callable[[], LLMProvider]) -> None:
        self._llm_factory = llm_factory

    async def generate_stream(
        self, natural_description: str, stop_event: asyncio.Event | None = None
    ) -> AsyncGenerator[dict[str, Any], None]:
        if self._llm_factory is None:
            yield {
                "type": "error",
                "data": {"message": "LLM factory not configured for agent generation"},
            }
            return

        yield {
            "type": "generating",
            "data": {"phase": "start", "message": "Starting generation..."},
        }

        llm = self._llm_factory()
        messages = [
            system_message(_AGENT_GENERATION_SYSTEM_PROMPT),
            user_message(
                [
                    {
                        "type": "text",
                        "text": f"Create an agent for: {natural_description}",
                    }
                ]
            ),
        ]

        stream = await llm.chat(
            messages=messages,
            tools=[],
            temperature=0.3,
            max_tokens=4096,
            stop_event=stop_event,
        )

        content_parts: list[str] = []
        last_report_len = 0
        async for chunk in stream:
            if chunk.content:
                content_parts.append(chunk.content)
                current = "".join(content_parts)
                if len(current) - last_report_len > 200:
                    last_report_len = len(current)
                    yield {
                        "type": "generating",
                        "data": {
                            "phase": "progress",
                            "content_preview": current[-300:],
                        },
                    }

        raw = (
            stream.response.content
            if stream.response.content
            else "".join(content_parts)
        )

        json_str = raw.strip()
        if json_str.startswith("```"):
            lines = json_str.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            json_str = "\n".join(lines)

        try:
            data: dict[str, Any] = json.loads(json_str)
        except json.JSONDecodeError:
            logger.error("agent.generated.parse_failed content=%s", raw[:500])
            yield {
                "type": "error",
                "data": {"message": "Failed to parse LLM output as JSON"},
            }
            return

        agent = GeneratedAgentMeta(
            name=data.get("name", ""),
            display_name=data.get("display_name", ""),
            description=data.get("description", ""),
            system_prompt=data.get("system_prompt", ""),
            provider=data.get("provider", "openai"),
            model=data.get("model", ""),
            llm_config=data.get("llm_config", {}),
        )

        yield {"type": "generated", "data": agent_to_dict(agent)}
