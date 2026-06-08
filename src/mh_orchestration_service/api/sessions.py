from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from mh_orchestration_service.api.dependencies import resolve_request_identity
from mh_orchestration_service.api.locale import parse_locale, resolve_display_name
from mh_orchestration_service.services.database import get_session_store

logger = logging.getLogger("orchestration.sessions")

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


class SessionCreateRequest(BaseModel):
    agent_name: str
    scenario_id: str | None = None


@router.get("")
async def list_sessions(
    request: Request,
    scenario_id: str | None = Query(None),
    user_id: str = Depends(resolve_request_identity),
):
    logger.debug("INBOUND list_sessions — user=%s scenario_id=%s", user_id, scenario_id)
    locale = parse_locale(request.headers.get("accept-language"))
    store = await get_session_store()
    sessions = await store.list_user_sessions(user_id, scenario_id)
    return [
        {
            "memory_id": s["session_id"],
            "title": s.get("title") or "Untitled",
            "created_at": s["created_at"],
            "message_count": s["message_count"],
            "agent_name": s["agent_name"],
            "user_id": s["user_id"],
            "scenario_id": s["scenario_id"],
            "display_name": resolve_display_name(
                s["agent_name"],
                s.get("display_name_locale"),
                locale,
            ),
        }
        for s in sessions
    ]


@router.post("")
async def create_session(
    request: Request,
    body: SessionCreateRequest,
    user_id: str = Depends(resolve_request_identity),
):
    logger.debug(
        "INBOUND create_session — user=%s agent=%s scenario_id=%s",
        user_id,
        body.agent_name,
        body.scenario_id,
    )
    locale = parse_locale(request.headers.get("accept-language"))

    display_name_locale: str | None = None
    adapters = request.app.state.adapters
    if adapters.management_provider is not None:
        agent_meta = await adapters.management_provider.get_agent(body.agent_name)
        if agent_meta is not None:
            display_name_locale = agent_meta.get("display_name_locale")

    store = await get_session_store()
    session = await store.create_session(
        agent_name=body.agent_name,
        user_id=user_id,
        scenario_id=body.scenario_id,
        display_name_locale=display_name_locale,
    )
    return {
        "memory_id": session.session_id,
        "title": session.title or "New Chat",
        "created_at": session.created_at,
        "message_count": 0,
        "agent_name": session.agent_name,
        "user_id": session.user_id,
        "scenario_id": session.scenario_id,
        "display_name": resolve_display_name(
            session.agent_name,
            display_name_locale,
            locale,
        ),
    }


@router.get("/{memory_id}")
async def get_session(
    request: Request,
    memory_id: str,
    user_id: str = Depends(resolve_request_identity),
):
    store = await get_session_store()
    session = await store.get_session(memory_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    locale = parse_locale(request.headers.get("accept-language"))
    return {
        "memory_id": session.session_id,
        "title": session.title or "Untitled",
        "created_at": session.created_at,
        "message_count": len(session.get_all_messages()),
        "agent_name": session.agent_name,
        "user_id": session.user_id,
        "scenario_id": session.scenario_id,
        "display_name": resolve_display_name(
            session.agent_name,
            session.display_name_locale,
            locale,
        ),
    }


@router.get("/{memory_id}/messages")
async def get_session_messages(
    request: Request,
    memory_id: str,
    user_id: str = Depends(resolve_request_identity),
):
    store = await get_session_store()
    session = await store.get_session(memory_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    messages = store.get_messages_as_items(session)
    return messages


@router.delete("/{memory_id}", status_code=200)
async def delete_session(
    request: Request,
    memory_id: str,
    user_id: str = Depends(resolve_request_identity),
):
    store = await get_session_store()
    session = await store.get_session(memory_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    await store.delete_session(memory_id)
    return {"ok": True}
