from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from minimal_harness.auth import match_permission
from minimal_harness.memory import system_message, user_message
from minimal_harness.types import (
    AgentEnd,
    ExecutionEnd,
    ExecutionStart,
    LLMEnd,
    LLMStart,
    MessageEvent,
    ToolEnd,
    ToolStart,
)

from mh_orchestration_service.api.dependencies import verify_m2m_request
from mh_orchestration_service.api.locale import (
    parse_locale,
    resolve_description,
    resolve_display_name,
)
from mh_orchestration_service.services.database import get_session_store
from mh_orchestration_service.services.runtime_service import (
    _get_permitted_scenario_agents,
    acquire_session_lock,
    create_runtime,
    release_session_lock,
)

logger = logging.getLogger("orchestration.runtime_tools")

router = APIRouter(prefix="/api/v1/tools", tags=["runtime_tools"])


def _sse_line(event_type: str, data: Any) -> str:
    return f"data: {json.dumps({'type': event_type, 'data': data}, ensure_ascii=False, default=str)}\n\n"


@router.post("/calculator/execute")
async def calculator_execute(
    request: Request,
    body: dict[str, Any],
    app_id: str = Depends(verify_m2m_request),
):
    args = body.get("args", {})
    expression = args.get("expression", "")

    async def event_stream():
        try:
            yield _sse_line(
                "tool_progress",
                {"message": f"Evaluating: {expression}"},
            )
            allowed = {"__builtins__": {}}
            result = eval(expression, allowed)  # noqa: PGH001
            yield _sse_line(
                "tool_end",
                {
                    "status": "ok",
                    "expression": expression,
                    "result": result,
                },
            )
        except Exception as e:
            yield _sse_line("error", {"message": str(e)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/discover_agents/execute")
async def discover_agents_execute(
    request: Request,
    body: dict[str, Any],
    accept_language: str | None = Header(None, alias="Accept-Language"),
    app_id: str = Depends(verify_m2m_request),
):
    args = body.get("args", {})
    locale = args.get("locale") or parse_locale(accept_language)
    exclude = args.get("exclude")
    scenario_id = request.query_params.get("scenario_id", "")

    async def event_stream():
        adapters = request.app.state.adapters
        scenario_agent_names = await _get_permitted_scenario_agents(
            adapters.management_provider,
            adapters.permission_checker,
            scenario_id,
            app_id,
        )

        user_perms: list[str] | None = None
        if scenario_agent_names is None:
            if adapters.permission_checker:
                user_perms = await adapters.permission_checker.get_permissions(app_id)

        agents = await adapters.management_provider.list_agents()
        result = []
        for a in agents:
            name = a["name"]
            if exclude and name == exclude:
                continue
            if scenario_agent_names is not None and name not in scenario_agent_names:
                continue
            if user_perms is not None and not match_permission(
                user_perms, f"use:agent:{name}"
            ):
                continue
            result.append(
                {
                    "name": a["name"],
                    "display_name": resolve_display_name(
                        a.get("display_name", a["name"]),
                        a.get("display_name_locale"),
                        locale,
                    ),
                    "description": resolve_description(
                        a.get("description", ""),
                        a.get("description_locale"),
                        locale,
                    ),
                }
            )
        yield _sse_line("tool_end", {"status": "ok", "agents": result})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/handoff/execute")
async def handoff_execute(
    request: Request,
    body: dict[str, Any],
    accept_language: str | None = Header(None, alias="Accept-Language"),
    app_id: str = Depends(verify_m2m_request),
):
    args = body.get("args", {})
    target_agent_name = args.get("target_agent_name", "")
    context_summary = args.get("context_summary", "")
    task_description = args.get("task_description", "")
    locale = args.get("locale") or parse_locale(accept_language)
    user_id = app_id

    if not target_agent_name:

        async def _error_stream():
            yield _sse_line("error", {"message": "target_agent_name is required"})

        return StreamingResponse(_error_stream(), media_type="text/event-stream")

    scenario_id = request.query_params.get("scenario_id", "")

    async def event_stream():
        adapters = request.app.state.adapters

        agent_meta = await adapters.management_provider.get_agent(target_agent_name)
        if agent_meta is None:
            yield _sse_line(
                "error", {"message": f"Handoff target '{target_agent_name}' not found"}
            )
            return

        combined = f"Context: {context_summary}\n\nTask: {task_description}"

        handoff_session_id = uuid.uuid4().hex
        store = await get_session_store()
        await store.create_session(
            session_id=handoff_session_id,
            agent_name=target_agent_name,
            user_id=user_id,
            scenario_id=scenario_id,
            display_name_locale=agent_meta.get("display_name_locale"),
        )

        lock = await acquire_session_lock(handoff_session_id)
        sub_task = None
        sub_stop_event = None
        result_text = ""

        try:
            runtime, _agent_registry, _tool_registry, _ = await create_runtime(
                request=request,
                user_id=user_id,
                agent_name=target_agent_name,
                tool_names=[],
                session_store=store,
                session_id=handoff_session_id,
                scenario_id=scenario_id,
            )

            sub_task, sub_stop_event, queue = await runtime.run(
                user_input=[{"type": "text", "text": combined}],
                agent_metadata_id=target_agent_name,
                memory_id=handoff_session_id,
                context={"locale": locale, "agent_name": target_agent_name},
            )

            yield _sse_line(
                "tool_progress",
                {
                    "status": "handoff_started",
                    "message": f"Starting delegated task to {target_agent_name}...",
                },
            )

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if sub_stop_event.is_set():
                        yield _sse_line(
                            "tool_progress",
                            {
                                "status": "error",
                                "message": "Delegated task was interrupted",
                            },
                        )
                        break
                    continue

                if event is None:
                    break

                if isinstance(event, MessageEvent):
                    continue

                if isinstance(event, LLMStart):
                    yield _sse_line(
                        "tool_progress",
                        {
                            "status": "progress",
                            "type": "llm_start",
                            "message": "LLM generating...",
                        },
                    )
                elif isinstance(event, LLMEnd):
                    if event.content:
                        result_text = str(event.content)
                    msg = (event.content or "LLM response generated")[:200]
                    if event.error:
                        msg = f"[Error] {event.error}: {msg}"
                    yield _sse_line(
                        "tool_progress",
                        {
                            "status": "progress",
                            "type": "llm_end",
                            "message": msg,
                        },
                    )
                elif isinstance(event, ExecutionStart):
                    names = ", ".join(tc["function"]["name"] for tc in event.tool_calls)
                    yield _sse_line(
                        "tool_progress",
                        {
                            "status": "progress",
                            "type": "execution_start",
                            "message": f"Executing: {names}",
                        },
                    )
                elif isinstance(event, ExecutionEnd):
                    parts = []
                    for tc, result in event.results:
                        name = tc["function"]["name"]
                        r = (str(result) if result is not None else "")[:200]
                        parts.append(f"{name} => {r}")
                    msg = " | ".join(parts) if parts else "Tool execution complete"
                    if event.error:
                        msg = f"[Error] {event.error}: {msg}"
                    yield _sse_line(
                        "tool_progress",
                        {
                            "status": "progress",
                            "type": "execution_end",
                            "message": msg,
                        },
                    )
                elif isinstance(event, ToolStart):
                    name = event.tool_call["function"]["name"]
                    yield _sse_line(
                        "tool_progress",
                        {
                            "status": "progress",
                            "type": "tool_start",
                            "message": f"Tool started: {name}",
                        },
                    )
                elif isinstance(event, ToolEnd):
                    name = event.tool_call["function"]["name"]
                    result_str = (
                        str(event.result) if event.result is not None else ""
                    )[:200]
                    yield _sse_line(
                        "tool_progress",
                        {
                            "status": "progress",
                            "type": "tool_end",
                            "message": f"Tool {name} completed: {result_str}",
                        },
                    )
                elif isinstance(event, AgentEnd):
                    result_text = event.response or result_text
                    yield _sse_line(
                        "tool_progress",
                        {
                            "status": "progress",
                            "type": "agent_end",
                            "message": (event.response or "Agent completed")[:200],
                        },
                    )

            yield _sse_line(
                "tool_end",
                {
                    "status": "handoff_complete",
                    "message": "Delegated task completed",
                    "result": result_text,
                },
            )

        except Exception:
            logger.exception("Handoff execution error")
            yield _sse_line(
                "error", {"message": "An internal error occurred during handoff"}
            )
        finally:
            if sub_stop_event is not None:
                sub_stop_event.set()
            if sub_task is not None:
                sub_task.cancel()
                try:
                    await sub_task
                except (asyncio.CancelledError, Exception):
                    pass
            await store.delete_session(handoff_session_id)
            await release_session_lock(handoff_session_id, lock)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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


GENERAL_VIZ_SYSTEM_PROMPT = """You are a data visualization expert. Generate a complete, self-contained HTML page that visualizes the data described by the user.

Requirements:
- Use inline CSS only (no external stylesheets)
- No external dependencies (no CDN scripts, no fonts from external sources)
- The HTML page should be visually appealing, with proper colors, layout, and typography
- Use appropriate chart types (bars, lines, tables, cards, etc.) based on the data
- Include a title and clear labels
- Make the page responsive
- Output ONLY the raw HTML code, no markdown fences, no explanations"""

GENERAL_VIZ_SYSTEM_PROMPT_ZH = """你是一个数据可视化专家。根据用户的描述生成一个完整的、自包含的HTML页面，用于可视化数据。

要求：
- 仅使用内联 CSS（不使用外部样式表）
- 无外部依赖（不使用 CDN 脚本、外部字体等）
- HTML 页面应美观，具有合适的颜色、布局和排版
- 根据数据使用合适的图表类型（柱状图、折线图、表格、卡片等）
- 包含标题和清晰的标签
- 页面需响应式
- 只输出原始 HTML 代码，不要用 markdown 代码块包裹，不要任何解释"""


def _extract_html(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return raw
    lines = raw.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
        end_idx = len(lines)
        for i, line in enumerate(lines):
            if line.strip() == "```":
                end_idx = i
                break
        lines = lines[:end_idx]
        return "\n".join(lines).strip()
    return raw


@router.post("/general_visualization/execute")
async def general_visualization_execute(
    request: Request,
    body: dict[str, Any],
    accept_language: str | None = Header(None, alias="Accept-Language"),
    app_id: str = Depends(verify_m2m_request),
):
    args = body.get("args", {})
    description = args.get("description", "")
    locale = args.get("locale") or parse_locale(accept_language)
    is_zh = locale == "zh"

    progress_initial = (
        "正在优化展示形式..." if is_zh else "Optimizing display format..."
    )
    progress_template = (
        "正在优化展示形式...（{} 字符）"
        if is_zh
        else "Optimizing display format... ({} chars)"
    )
    content_ok = (
        "可视化图表已经在上方渲染展示给用户。数据已通过图表直观呈现。"
        "请勿重复描述图表中的数据。直接告知用户查看上方图表即可，简短回应。"
        if is_zh
        else "A visual chart has been rendered above and is visible to the user. "
        "The data is already presented visually. "
        "DO NOT repeat or re-describe the visualized data. "
        "Simply tell the user to look at the chart above for details."
    )
    content_error = (
        "展示优化失败，请稍后重试。"
        if is_zh
        else "Display optimization failed. Please retry later."
    )
    timeout_msg = (
        "展示优化超时，请重试。"
        if is_zh
        else "Display optimization timed out. Please retry."
    )
    system_prompt = GENERAL_VIZ_SYSTEM_PROMPT_ZH if is_zh else GENERAL_VIZ_SYSTEM_PROMPT

    async def event_stream():
        try:
            yield _sse_line(
                "tool_progress",
                {"message": progress_initial},
            )

            adapters = request.app.state.adapters
            registry = getattr(adapters, "llm_provider_registry", None)
            if registry is None:
                yield _sse_line(
                    "error", {"message": "LLM provider registry not configured"}
                )
                return

            viz_provider_name = "openai_viz"
            if not registry.is_registered(viz_provider_name):
                viz_provider_name = "openai"

            llm = registry.create(viz_provider_name, {})

            messages = [
                system_message(system_prompt),
                user_message([{"type": "text", "text": description}]),
            ]
            stream = await llm.chat(
                messages=messages,
                tools=[],
                temperature=0.2,
                max_tokens=4096,
            )

            accumulated = ""
            last_report_len = 0
            REPORT_INTERVAL = 50
            stream_iter = stream.__aiter__()
            while True:
                try:
                    chunk = await asyncio.wait_for(stream_iter.__anext__(), timeout=60)
                except asyncio.TimeoutError:
                    yield _sse_line("error", {"message": timeout_msg})
                    return
                except StopAsyncIteration:
                    break
                delta = chunk.content or ""
                if not delta:
                    continue
                accumulated += delta
                if len(accumulated) - last_report_len >= REPORT_INTERVAL:
                    yield _sse_line(
                        "tool_progress",
                        {"message": progress_template.format(len(accumulated))},
                    )
                    last_report_len = len(accumulated)

            raw_html = _extract_html(accumulated)

            yield _sse_line(
                "tool_end",
                {
                    "content": content_ok,
                    "__meta": {"html": raw_html},
                },
            )
        except Exception:
            logger.exception("General visualization execution error")
            yield _sse_line(
                "tool_end",
                {
                    "content": content_error,
                    "__meta": {"html": ""},
                },
            )

    return StreamingResponse(event_stream(), media_type="text/event-stream")
