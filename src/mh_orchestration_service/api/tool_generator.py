from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from mh_orchestration_service.api.dependencies import (
    get_current_user,
    verify_m2m_request,
)
from mh_orchestration_service.services.generated_tool_executor import (
    execute_generated_tool,
)

logger = logging.getLogger("orchestration.tool_generator")

tool_generator_router = APIRouter(
    prefix="/api/v1/tool-generator", tags=["tool-generator"]
)
generated_execute_router = APIRouter(prefix="/api/v1/tools", tags=["generated-tools"])


class GenerateRequest(BaseModel):
    natural_description: str


class SaveToolRequest(BaseModel):
    name: str
    display_name: str
    description: str
    parameters: dict[str, Any]
    source_code: str


class UpdateToolRequest(BaseModel):
    display_name: str
    description: str
    parameters: dict[str, Any]
    source_code: str


# ── Generation endpoint (uses ToolGenerator) ──


@tool_generator_router.post("/generate")
async def generate_tool(
    request: Request,
    body: GenerateRequest,
    user_id: str = Depends(get_current_user),
) -> StreamingResponse:
    adapters = request.app.state.adapters
    generator = adapters.generated_tool_provider
    if generator is None:
        return StreamingResponse(
            iter([_sse("error", {"message": "ToolGenerator not configured"})]),
            media_type="text/event-stream",
        )

    async def _stream():
        stop_event = asyncio.Event()
        try:
            async for event in generator.generate_stream(
                body.natural_description, stop_event=stop_event
            ):
                if await request.is_disconnected():
                    logger.info("tool.generate.disconnect — client disconnected")
                    stop_event.set()
                    break
                yield _sse(event["type"], event["data"])
        except Exception as e:
            if not await request.is_disconnected():
                logger.exception("tool.generate.error")
                yield _sse("error", {"message": str(e)})

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
    )


# ── CRUD endpoints — delegate to management_provider ──


@tool_generator_router.post("/tools")
async def save_tool(
    request: Request,
    body: SaveToolRequest,
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    adapters = request.app.state.adapters
    mgmt = adapters.management_provider
    if mgmt is None:
        raise HTTPException(500, "Management provider not configured")

    payload = {
        "name": body.name,
        "display_name": body.display_name,
        "description": body.description,
        "parameters": body.parameters,
        "source_code": body.source_code,
        "endpoint_url": f"/api/v1/tools/generated/{body.name}/execute",
        "created_by": user_id,
    }
    try:
        saved = await mgmt.create_tool(payload)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    except Exception as e:
        raise HTTPException(500, str(e)) from None

    return {
        "name": saved.get("name", ""),
        "display_name": saved.get("display_name", ""),
        "description": saved.get("description", ""),
        "parameters": saved.get("parameters", {}),
        "source_code": saved.get("source_code", ""),
        "user_id": saved.get("created_by", ""),
        "created_at": saved.get("created_at", ""),
        "updated_at": saved.get("updated_at", ""),
    }


@tool_generator_router.get("/tools")
async def list_tools(
    request: Request,
    user_id: str = Depends(get_current_user),
) -> list[dict[str, Any]]:
    adapters = request.app.state.adapters
    mgmt = adapters.management_provider
    if mgmt is None:
        return []

    tools = await mgmt.list_tools()
    result = []
    for t in tools:
        if t.get("created_by") != user_id:
            continue
        result.append(
            {
                "name": t.get("name", ""),
                "display_name": t.get("display_name", ""),
                "description": t.get("description", ""),
                "parameters": t.get("parameters", {}),
                "source_code": t.get("source_code", ""),
                "user_id": t.get("created_by", ""),
                "created_at": t.get("created_at", ""),
                "updated_at": t.get("updated_at", ""),
            }
        )
    return result


@tool_generator_router.put("/tools/{name}")
async def update_tool(
    request: Request,
    name: str,
    body: UpdateToolRequest,
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    adapters = request.app.state.adapters
    mgmt = adapters.management_provider
    if mgmt is None:
        raise HTTPException(500, "Management provider not configured")

    existing = await mgmt.get_tool(name)
    if existing is None or existing.get("created_by") != user_id:
        raise HTTPException(404, f"Tool '{name}' not found")

    now = datetime.now(UTC).isoformat()
    payload = {
        "display_name": body.display_name,
        "description": body.description,
        "parameters": body.parameters,
        "source_code": body.source_code,
        "endpoint_url": f"/api/v1/tools/generated/{name}/execute",
        "updated_by": user_id,
        "updated_at": now,
    }
    try:
        updated = await mgmt.update_tool(name, payload)
    except ValueError as e:
        raise HTTPException(404, str(e)) from None

    return {
        "name": updated.get("name", ""),
        "display_name": updated.get("display_name", ""),
        "description": updated.get("description", ""),
        "parameters": updated.get("parameters", {}),
        "source_code": updated.get("source_code", ""),
        "user_id": updated.get("created_by", ""),
        "created_at": updated.get("created_at", ""),
        "updated_at": updated.get("updated_at", ""),
    }


@tool_generator_router.delete("/tools/{name}")
async def delete_tool(
    request: Request,
    name: str,
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    adapters = request.app.state.adapters
    mgmt = adapters.management_provider
    if mgmt is None:
        raise HTTPException(500, "Management provider not configured")

    existing = await mgmt.get_tool(name)
    if existing is None or existing.get("created_by") != user_id:
        raise HTTPException(404, f"Tool '{name}' not found")

    try:
        await mgmt.delete_tool(name)
    except ValueError as e:
        raise HTTPException(404, str(e)) from None
    return {"status": "deleted", "name": name}


# ── Execution endpoints (M2M for chat, User for trial) ──


def _sse(event_type: str, data: Any) -> str:
    return f"data: {json.dumps({'type': event_type, 'data': data}, ensure_ascii=False, default=str)}\n\n"


async def _stream_tool_execution(
    mgmt,
    name: str,
    body: dict[str, Any],
    *,
    source_code: str | None = None,
):
    args = body.get("args", {})

    if source_code is None:
        tool = await mgmt.get_tool(name)
        if tool is None:
            yield _sse("error", {"message": f"Tool '{name}' not found"})
            return
        source_code = tool.get("source_code", "")
        required_args = tool.get("parameters", {}).get("required", [])
        for key in required_args:
            if key not in args:
                yield _sse("error", {"message": f"Missing required argument: '{key}'"})
                return

    if not source_code:
        yield _sse("error", {"message": f"Tool '{name}' has no source code"})
        return

    final_result = None
    try:
        async for chunk in execute_generated_tool(source_code, args, name):
            if isinstance(chunk, dict) and "error" in chunk:
                yield _sse("error", {"message": chunk["error"]})
                return
            final_result = chunk
            yield _sse("tool_progress", chunk)
        if final_result is not None:
            yield _sse("tool_end", final_result)
        else:
            yield _sse("tool_end", {"status": "ok"})
    except Exception as e:
        logger.exception("tool.generated.execute.error name=%s", name)
        yield _sse("error", {"message": str(e)})


@generated_execute_router.post("/generated/{name}/execute")
async def execute_tool(
    request: Request,
    name: str,
    body: dict[str, Any],
    app_id: str = Depends(verify_m2m_request),
) -> StreamingResponse:
    adapters = request.app.state.adapters
    mgmt = adapters.management_provider
    if mgmt is None:
        return StreamingResponse(
            iter([_sse("error", {"message": "Management provider not configured"})]),
            media_type="text/event-stream",
        )
    return StreamingResponse(
        _stream_tool_execution(mgmt, name, body),
        media_type="text/event-stream",
    )


@tool_generator_router.post("/tools/{name}/trial")
async def trial_tool(
    request: Request,
    name: str,
    body: dict[str, Any],
    user_id: str = Depends(get_current_user),
) -> StreamingResponse:
    adapters = request.app.state.adapters
    mgmt = adapters.management_provider
    if mgmt is None:
        return StreamingResponse(
            iter([_sse("error", {"message": "Management provider not configured"})]),
            media_type="text/event-stream",
        )
    source_code = body.get("source_code")
    if not source_code:
        return StreamingResponse(
            iter([_sse("error", {"message": "source_code is required"})]),
            media_type="text/event-stream",
        )
    return StreamingResponse(
        _stream_tool_execution(mgmt, name, body, source_code=source_code),
        media_type="text/event-stream",
    )
