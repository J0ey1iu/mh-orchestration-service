from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse

from mh_orchestration_service.api.dependencies import verify_m2m_request
from mh_orchestration_service.api.locale import parse_locale

logger = logging.getLogger("orchestration.runtime_tools_dev")

router = APIRouter(prefix="/api/v1/tools", tags=["runtime_tools_dev"])


def _sse_line(event_type: str, data: Any) -> str:
    return f"data: {json.dumps({'type': event_type, 'data': data}, ensure_ascii=False, default=str)}\n\n"


@router.post("/show_ui_meta/execute")
async def show_ui_meta_execute(
    request: Request,
    body: dict[str, Any],
    app_id: str = Depends(verify_m2m_request),
):
    result_meta = {
        "profiles": [
            {
                "name": "Alice",
                "role": "Software Engineer",
                "experience": "5 years",
                "skills": ["Python", "Rust", "Kubernetes"],
                "avatar": "👩‍💻",
            },
            {
                "name": "Bob",
                "role": "Product Manager",
                "experience": "8 years",
                "skills": ["Strategy", "Analytics", "UX"],
                "avatar": "👨‍💼",
            },
            {
                "name": "Charlie",
                "role": "Data Scientist",
                "experience": "3 years",
                "skills": ["ML", "Python", "SQL"],
                "avatar": "🧑‍🔬",
            },
        ],
        "chart_data": {
            "labels": ["Alice", "Bob", "Charlie"],
            "values": [5, 8, 3],
            "label": "Years of Experience",
        },
        "html": (
            '<div style="padding:10px;background:#f0f7ff;border-radius:8px;'
            'border:1px solid #b8d4fe;">'
            '<h3 style="margin:0 0 8px;color:#1a56db;">Profile Matches</h3>'
            '<p style="margin:0;color:#374151;">3 candidates matched your search criteria. '
            "Click each card for details.</p>"
            "</div>"
        ),
    }
    result_content = json.dumps(
        {
            "result": "Found 3 matching profiles: Alice (SDE, 5yr), "
            "Bob (PM, 8yr), Charlie (DS, 3yr). "
            "All profiles match the query criteria.",
            "count": 3,
        },
        ensure_ascii=False,
    )

    async def event_stream():
        yield _sse_line(
            "tool_progress",
            {"message": "Fetching profile data..."},
        )
        yield _sse_line(
            "tool_end",
            {"content": result_content, "__meta": result_meta},
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/stop_agent/execute")
async def stop_agent_execute(
    request: Request,
    body: dict[str, Any],
    accept_language: str | None = Header(None, alias="Accept-Language"),
    app_id: str = Depends(verify_m2m_request),
):
    args = body.get("args", {})
    message = args.get("message", "Agent stopped by tool request")
    locale = args.get("locale") or parse_locale(accept_language)
    is_zh = locale == "zh"
    progress_msg = (
        "执行完成，标记 agent 停止..." if is_zh else "Done, stopping agent..."
    )

    async def event_stream():
        yield _sse_line(
            "tool_progress",
            {"message": progress_msg},
        )
        yield _sse_line(
            "tool_end",
            {
                "content": message,
                "__stop": True,
            },
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream")
