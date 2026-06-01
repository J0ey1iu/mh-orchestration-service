from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from minimal_harness.memory import system_message, user_message
from pydantic import BaseModel

from mh_orchestration_service.api.dependencies import get_current_user

logger = logging.getLogger("orchestration.agent_generator")

agent_generator_router = APIRouter(
    prefix="/api/v1/agent-generator", tags=["agent-generator"]
)


class GenerateRequest(BaseModel):
    natural_description: str


class SaveAgentRequest(BaseModel):
    name: str
    display_name: str
    description: str
    system_prompt: str
    provider: str = "openai"
    model: str = ""
    llm_config: dict[str, Any] = {}


class UpdateAgentRequest(BaseModel):
    display_name: str
    description: str
    system_prompt: str
    provider: str = "openai"
    model: str = ""
    llm_config: dict[str, Any] = {}


# ── Generation endpoint (uses AgentGenerator) ──


@agent_generator_router.post("/generate")
async def generate_agent(
    request: Request,
    body: GenerateRequest,
    user_id: str = Depends(get_current_user),
) -> StreamingResponse:
    adapters = request.app.state.adapters
    generator = adapters.generated_agent_provider
    if generator is None:
        return StreamingResponse(
            iter([_sse("error", {"message": "AgentGenerator not configured"})]),
            media_type="text/event-stream",
        )

    async def _stream():
        try:
            async for event in generator.generate_stream(body.natural_description):
                yield _sse(event["type"], event["data"])
        except Exception as e:
            logger.exception("agent.generate.error")
            yield _sse("error", {"message": str(e)})

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
    )


# ── CRUD endpoints — delegate to management_provider ──


@agent_generator_router.post("/agents")
async def save_agent(
    request: Request,
    body: SaveAgentRequest,
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    mgmt = request.app.state.adapters.management_provider
    if mgmt is None:
        raise HTTPException(500, "Management provider not configured")

    payload = {
        "name": body.name,
        "display_name": body.display_name,
        "description": body.description,
        "system_prompt": body.system_prompt,
        "provider": body.provider,
        "model": body.model,
        "llm_config": body.llm_config,
        "created_by": user_id,
    }
    try:
        saved = await mgmt.create_agent(payload)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    except Exception as e:
        raise HTTPException(500, str(e)) from None

    return {
        "name": saved.get("name", ""),
        "display_name": saved.get("display_name", ""),
        "description": saved.get("description", ""),
        "system_prompt": saved.get("system_prompt", ""),
        "provider": saved.get("provider", "openai"),
        "model": saved.get("model", ""),
        "llm_config": saved.get("llm_config", {}),
        "user_id": saved.get("created_by", ""),
        "created_at": saved.get("created_at", ""),
        "updated_at": saved.get("updated_at", ""),
    }


@agent_generator_router.get("/agents")
async def list_agents(
    request: Request,
    user_id: str = Depends(get_current_user),
) -> list[dict[str, Any]]:
    mgmt = request.app.state.adapters.management_provider
    if mgmt is None:
        return []

    agents = await mgmt.list_agents()
    result = []
    for a in agents:
        if a.get("created_by") != user_id:
            continue
        result.append(
            {
                "name": a.get("name", ""),
                "display_name": a.get("display_name", ""),
                "description": a.get("description", ""),
                "system_prompt": a.get("system_prompt", ""),
                "provider": a.get("provider", "openai"),
                "model": a.get("model", ""),
                "llm_config": a.get("llm_config", {}),
                "user_id": a.get("created_by", ""),
                "created_at": a.get("created_at", ""),
                "updated_at": a.get("updated_at", ""),
            }
        )
    return result


@agent_generator_router.put("/agents/{name}")
async def update_agent(
    request: Request,
    name: str,
    body: UpdateAgentRequest,
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    mgmt = request.app.state.adapters.management_provider
    if mgmt is None:
        raise HTTPException(500, "Management provider not configured")

    existing = await mgmt.get_agent(name)
    if existing is None or existing.get("created_by") != user_id:
        raise HTTPException(404, f"Agent '{name}' not found")

    now = datetime.now(UTC).isoformat()
    payload = {
        "display_name": body.display_name,
        "description": body.description,
        "system_prompt": body.system_prompt,
        "provider": body.provider,
        "model": body.model,
        "llm_config": body.llm_config,
        "updated_by": user_id,
        "updated_at": now,
    }
    try:
        updated = await mgmt.update_agent(name, payload)
    except ValueError as e:
        raise HTTPException(404, str(e)) from None

    return {
        "name": updated.get("name", ""),
        "display_name": updated.get("display_name", ""),
        "description": updated.get("description", ""),
        "system_prompt": updated.get("system_prompt", ""),
        "provider": updated.get("provider", "openai"),
        "model": updated.get("model", ""),
        "llm_config": updated.get("llm_config", {}),
        "user_id": updated.get("created_by", ""),
        "created_at": updated.get("created_at", ""),
        "updated_at": updated.get("updated_at", ""),
    }


@agent_generator_router.delete("/agents/{name}")
async def delete_agent(
    request: Request,
    name: str,
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    mgmt = request.app.state.adapters.management_provider
    if mgmt is None:
        raise HTTPException(500, "Management provider not configured")

    existing = await mgmt.get_agent(name)
    if existing is None or existing.get("created_by") != user_id:
        raise HTTPException(404, f"Agent '{name}' not found")

    try:
        await mgmt.delete_agent(name)
    except ValueError as e:
        raise HTTPException(404, str(e)) from None
    return {"status": "deleted", "name": name}


class TrialRequest(BaseModel):
    message: str
    system_prompt: str = ""
    provider: str = "openai"
    model: str = ""
    llm_config: dict[str, Any] = {}


@agent_generator_router.post("/agents/{name}/trial")
async def trial_agent(
    request: Request,
    name: str,
    body: TrialRequest,
    user_id: str = Depends(get_current_user),
) -> StreamingResponse:
    adapters = request.app.state.adapters
    registry = getattr(adapters, "llm_provider_registry", None)
    if registry is None:
        return StreamingResponse(
            iter([_sse("error", {"message": "LLM registry not configured"})]),
            media_type="text/event-stream",
        )

    system_prompt = body.system_prompt

    async def _stream():
        try:
            model_cfg = {"model": body.model} if body.model else {}
            llm = registry.create(body.provider, model_cfg)
            messages = [
                system_message(system_prompt),
                user_message([{"type": "text", "text": body.message}]),
            ]
            stream = await llm.chat(
                messages=messages,
                tools=[],
                temperature=(body.llm_config or {}).get("temperature", 0.7),
                max_tokens=(body.llm_config or {}).get("max_tokens", 4096),
            )

            accumulated = ""
            async for chunk in stream:
                if chunk.content:
                    accumulated += chunk.content
                    yield _sse("chunk", {"content": chunk.content})

            yield _sse("end", {"content": accumulated})
        except Exception as e:
            logger.exception("agent.trial.error name=%s", name)
            yield _sse("error", {"message": str(e)})

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
    )


def _sse(event_type: str, data: Any) -> str:
    return f"data: {json.dumps({'type': event_type, 'data': data}, ensure_ascii=False, default=str)}\n\n"
