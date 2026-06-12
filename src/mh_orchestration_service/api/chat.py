from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from mh_orchestration_service.auth import match_permission
from minimal_harness.memory_store import SessionStoreProtocol
from minimal_harness.tool.registry import ToolRegistry
from minimal_harness.types import (
    AgentEnd,
    AgentStart,
    ExecutionEnd,
    ExecutionStart,
    LLMChunk,
    LLMEnd,
    LLMStart,
    MemoryUpdate,
    MessageEvent,
    ToolEnd,
    ToolProgress,
    ToolResult,
    ToolStart,
)
from pydantic import BaseModel

from mh_orchestration_service.api.dependencies import (
    resolve_request_identity,
    resolve_request_permissions,
)
from mh_orchestration_service.api.locale import parse_locale
from mh_orchestration_service.context import get_current_trace_id
from mh_orchestration_service.services.database import get_session_store
from mh_orchestration_service.services.runtime_service import (
    acquire_session_lock,
    create_runtime,
    release_session_lock,
)

logger = logging.getLogger("orchestration.chat")

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str


async def _resolve_tool_display_name(
    func_name: str, locale: str, tool_registry: ToolRegistry | None
) -> str:
    if not locale or not func_name or not tool_registry:
        return func_name
    tool_meta = await tool_registry.get(func_name)
    if tool_meta:
        return tool_meta.resolve_display_name(locale)
    return func_name


def _compute_llm_start_info(event: LLMStart) -> dict[str, Any]:
    total_chars = 0
    for msg in event.messages:
        role = msg.get("role", "")
        if role == "system":
            total_chars += len(msg.get("content", "") or "")
        elif role == "user":
            parts = msg.get("content", [])
            if isinstance(parts, list):
                for part in parts:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total_chars += len(part.get("text", "") or "")
        elif role == "assistant":
            total_chars += len(msg.get("content", "") or "")
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                total_chars += len(json.dumps(tool_calls, ensure_ascii=False))
        elif role == "tool":
            total_chars += len(msg.get("content", "") or "")
        elif role == "reasoning":
            total_chars += len(msg.get("content", "") or "")
    return {
        "tool_names": [
            t.get("function", {}).get("name")
            if isinstance(t, dict)
            else getattr(t, "name", str(t))
            for t in event.tools
        ],
        "message_count": len(event.messages),
        "total_chars": total_chars,
    }


def _serialize_event(event: Any) -> dict[str, Any]:
    match event:
        case AgentStart():
            return {}
        case AgentEnd():
            return {
                "response": event.response,
                "time_taken": event.time_taken,
                "exceeded": event.exceeded,
                "interrupted": event.interrupted,
                "error": event.error,
            }
        case LLMStart():
            return _compute_llm_start_info(event)
        case LLMChunk():
            if event.chunk:
                return {
                    "content": event.chunk.content,
                    "reasoning": event.chunk.reasoning,
                    "tool_calls": event.chunk.tool_calls,
                }
            return {}
        case LLMEnd():
            return {
                "content": event.content,
                "reasoning_content": event.reasoning_content,
                "tool_calls": event.tool_calls,
                "usage": event.usage,
                "error": event.error,
            }
        case ExecutionStart():
            return {"tool_calls": event.tool_calls}
        case ExecutionEnd():
            return {
                "results": event.results,
                "error": event.error,
                "should_stop": event.should_stop,
                "response_text": event.response_text,
            }
        case ToolStart():
            return {
                "tool_call": event.tool_call,
                "display_name": (
                    event.tool_call.get("function", {}).get("name", "")
                    if isinstance(event.tool_call, dict)
                    else ""
                ),
            }
        case ToolProgress():
            return {
                "tool_call": event.tool_call,
                "chunk": _serialize_chunk(event.chunk),
            }
        case ToolEnd():
            if isinstance(event.result, ToolResult):
                return {
                    "tool_call": event.tool_call,
                    "result": _serialize_result(event.result.content),
                    "meta": event.result.meta,
                    "stop": event.result.stop,
                }
            return {
                "tool_call": event.tool_call,
                "result": _serialize_result(event.result),
            }
        case MemoryUpdate():
            return {"usage": event.usage}
    return {}


def _serialize_chunk(chunk: Any) -> Any:
    if isinstance(chunk, dict):
        return {k: v for k, v in chunk.items() if not k.startswith("_")}
    return str(chunk)


def _serialize_result(result: Any) -> Any:
    if isinstance(result, dict):
        return {k: v for k, v in result.items() if not k.startswith("_")}
    if isinstance(result, Exception):
        return f"[Error] {result}"
    if not isinstance(result, str):
        return str(result)
    return result


def _format_sse(event: str, data: dict[str, Any]) -> str:
    return (
        f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
    )


async def _get_scenario_for_session(
    request: Request,
    session,
) -> dict[str, Any] | None:
    scenario_id = session.scenario_id
    if not scenario_id:
        return None
    from mh_orchestration_service.api.scenarios import _get_scenario

    return await _get_scenario(request, scenario_id)


@router.post("/{memory_id}")
async def chat(
    request: Request,
    memory_id: str,
    body: ChatRequest,
    accept_language: str | None = Header(None, alias="Accept-Language"),
    user_id: str = Depends(resolve_request_identity),
    user_perms: list[str] = Depends(resolve_request_permissions),
) -> StreamingResponse:
    logger.debug(
        "INBOUND chat request — memory_id=%s user=%s locale=%s message_len=%d",
        memory_id,
        user_id,
        accept_language,
        len(body.message),
    )
    locale = parse_locale(accept_language)

    # Acquire per-session lock BEFORE loading session — this serialises
    # all concurrent requests targeting the same memory_id.
    lock = await acquire_session_lock(memory_id)
    try:
        store = await get_session_store()
        session = await store.get_session(memory_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.user_id != user_id:
            raise HTTPException(status_code=403, detail="Access denied")

        scenario = await _get_scenario_for_session(request, session)
        if not session.agent_name:
            raise HTTPException(status_code=400, detail="Session has no agent assigned")
        agent_name = session.agent_name
        tool_names: list[str] = []

        if scenario:
            for a in scenario.get("agents", []):
                if a["name"] == agent_name:
                    tool_names = a.get("tool_names", [])
                    break
            if not tool_names:
                for a in scenario.get("agents", []):
                    tool_names = a.get("tool_names", [])
                    agent_name = a["name"]
                    break
        tool_names = [
            t for t in tool_names if match_permission(user_perms, f"use:tool:{t}")
        ]

        scenario_id = session.scenario_id or ""
        trace_id = get_current_trace_id()

        async def _stream_with_lock():
            try:
                async for event in _stream_events(
                    request=request,
                    user_id=user_id,
                    message=body.message,
                    session=session,
                    memory_id=memory_id,
                    agent_name=agent_name,
                    tool_names=tool_names,
                    store=store,
                    locale=locale,
                    scenario_id=scenario_id,
                    trace_id=trace_id,
                ):
                    yield event
            finally:
                await release_session_lock(memory_id, lock)

        return StreamingResponse(
            _stream_with_lock(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception:
        await release_session_lock(memory_id, lock)
        raise


async def _stream_events(
    request: Request,
    user_id: str,
    message: str,
    session: Any,
    memory_id: str,
    agent_name: str,
    tool_names: list[str],
    store: SessionStoreProtocol,
    locale: str = "",
    scenario_id: str = "",
    trace_id: str = "",
) -> AsyncIterator[str]:
    task = None
    stop_event = None

    try:
        runtime, agent_registry, tool_registry, _ = await create_runtime(
            request=request,
            user_id=user_id,
            agent_name=agent_name,
            tool_names=tool_names,
            session_store=store,
            session_id=memory_id,
            scenario_id=scenario_id,
            trace_id=trace_id,
        )

        task, stop_event, queue = await runtime.run(
            user_input=[{"type": "text", "text": message}],
            agent_metadata_id=agent_name,
            memory_id=memory_id,
            tool_names=tool_names,
        )

        while True:
            event = await queue.get()
            if event is None:
                break

            if isinstance(event, MessageEvent):
                continue

            event_type = type(event).__name__
            payload = _serialize_event(event)
            if isinstance(event, ToolStart) and locale and tool_registry:
                func_name = (
                    event.tool_call.get("function", {}).get("name", "")
                    if isinstance(event.tool_call, dict)
                    else ""
                )
                payload["display_name"] = await _resolve_tool_display_name(
                    func_name, locale, tool_registry
                )
            logger.debug(
                "OUTBOUND event — event_type=%s memory_id=%s payload_keys=%s",
                event_type,
                memory_id,
                list(payload.keys()),
            )
            yield _format_sse(event_type, payload)
    except Exception:
        logger.exception("Chat stream error")
        yield _format_sse("Error", {"message": "An internal error occurred."})
    finally:
        if stop_event is not None:
            stop_event.set()
        if task is not None:
            await task

        if not session.title:
            session.title = message[:80]

        extra = {"title": session.title} if session.title else {}
        try:
            await store.save_memory(session.memory, memory_id, extra=extra)
        except Exception:
            logger.exception("Failed to persist session messages")

    yield _format_sse("done", {})
