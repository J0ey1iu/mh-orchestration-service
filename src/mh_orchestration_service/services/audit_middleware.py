from __future__ import annotations

import logging
from typing import Any

from minimal_harness.agent.middleware import Middleware
from minimal_harness.types import AgentEnd, LLMEnd, ToolCall

logger = logging.getLogger("orchestration.audit")


class AuditMiddleware(Middleware):
    """Logs agent lifecycle events for audit/tracing purposes.

    In production, replace the ``logger.info`` calls with writes to
    your audit store (Elasticsearch, database, S3, etc.).
    """

    def __init__(self, user_id: str, session_id: str) -> None:
        self._user_id = user_id
        self._session_id = session_id

    async def on_agent_start(self, user_input: Any) -> None:
        logger.info(
            "AUDIT agent_start user=%s session=%s input=%.200s",
            self._user_id,
            self._session_id,
            str(user_input),
        )

    async def on_agent_end(self, event: AgentEnd) -> None:
        logger.info(
            "AUDIT agent_end user=%s session=%s time_taken=%.2f error=%s",
            self._user_id,
            self._session_id,
            event.time_taken,
            event.error or "",
        )

    async def on_llm_start(self, messages: list[dict[str, Any]], tools: Any) -> None:
        logger.info(
            "AUDIT llm_start user=%s session=%s tool_count=%d",
            self._user_id,
            self._session_id,
            len(tools) if tools else 0,
        )

    async def on_llm_end(self, event: LLMEnd) -> None:
        usage = event.usage
        token_str = (
            f"prompt={usage['prompt_tokens']} completion={usage['completion_tokens']}"
            if usage
            else "unknown"
        )
        logger.info(
            "AUDIT llm_end user=%s session=%s tokens=(%s) error=%s",
            self._user_id,
            self._session_id,
            token_str,
            event.error or "",
        )

    async def on_tool_start(self, tool_call: ToolCall) -> None:
        logger.info(
            "AUDIT tool_start user=%s session=%s tool=%s",
            self._user_id,
            self._session_id,
            tool_call.get("function", {}).get("name", "unknown"),
        )

    async def on_tool_end(self, tool_call: ToolCall, result: Any) -> None:
        logger.info(
            "AUDIT tool_end user=%s session=%s tool=%s result=%.100s",
            self._user_id,
            self._session_id,
            tool_call.get("function", {}).get("name", "unknown"),
            str(result),
        )

    async def on_tool_error(self, tool_call: ToolCall, error: Exception) -> None:
        logger.warning(
            "AUDIT tool_error user=%s session=%s tool=%s error=%s",
            self._user_id,
            self._session_id,
            tool_call.get("function", {}).get("name", "unknown"),
            error,
        )
