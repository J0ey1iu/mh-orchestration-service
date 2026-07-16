from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from mh_orchestration_service.context import get_current_request, get_current_user_id


async def _web_search_fn(query: str = "") -> AsyncIterator[Any]:
    await asyncio.sleep(0.05)
    yield f'[Simulated] Search results for "{query}": Found 42 relevant pages. Top result: example.com/{query.replace(" ", "-")}'


async def _discover_agents_fn(
    exclude: str = "", locale: str = ""
) -> AsyncIterator[Any]:
    from mh_orchestration_service.api.locale import (
        resolve_description,
        resolve_display_name,
    )
    from mh_orchestration_service.auth import match_permission

    request = get_current_request()
    if request is None:
        yield {"status": "ok", "agents": []}
        return
    adapters = request.app.state.adapters
    identity = get_current_user_id() or ""

    agents = await adapters.management_provider.list_agents()
    user_perms: list[str] | None = None
    if adapters.permission_checker is not None:
        user_perms = await adapters.permission_checker.get_permissions(identity)

    result = []
    for a in agents:
        name = a["name"]
        if exclude and name == exclude:
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
    yield {"status": "ok", "agents": result}


async def _handoff_fn(
    target_agent_name: str = "",
    context_summary: str = "",
    task_description: str = "",
    locale: str = "",
) -> AsyncIterator[Any]:
    if not target_agent_name:
        yield {"status": "error", "message": "target_agent_name is required"}
        return

    from mh_orchestration_service.services.database import get_session_store
    from mh_orchestration_service.services.runtime_service import (
        acquire_session_lock,
        create_runtime,
        release_session_lock,
    )

    request = get_current_request()
    if request is None:
        yield {"status": "error", "message": "No request context"}
        return

    identity = get_current_user_id() or ""
    store = await get_session_store()

    import uuid

    handoff_session_id = f"mem_{uuid.uuid4().hex[:12]}"
    await store.create_session(
        session_id=handoff_session_id,
        agent_name=target_agent_name,
        user_id=identity,
        transient=True,
    )

    lock = await acquire_session_lock(handoff_session_id)
    sub_task = None
    sub_stop_event = None
    result_text = ""
    try:
        runtime, _agent_registry, _tool_registry, _ = await create_runtime(
            request=request,
            user_id=identity,
            agent_name=target_agent_name,
            tool_names=[],
            session_store=store,
            session_id=handoff_session_id,
        )

        combined = f"Context: {context_summary}\n\nTask: {task_description}"

        sub_task, sub_stop_event, queue = await runtime.run(
            user_input=[{"type": "text", "text": combined}],
            agent_metadata_id=target_agent_name,
            memory_id=handoff_session_id,
            context={"locale": locale, "agent_name": target_agent_name},
        )

        yield {
            "status": "progress",
            "type": "handoff_started",
            "message": f"Starting delegated task to {target_agent_name}...",
            "target_agent": target_agent_name,
            "task": task_description,
        }

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if sub_stop_event and sub_stop_event.is_set():
                    yield {"status": "error", "type": "interrupted"}
                    break
                continue

            if event is None:
                break

            from minimal_harness.types import (
                AgentEnd,
                ToolProgress,
            )

            if isinstance(event, AgentEnd):
                result_text = event.response or result_text
            elif isinstance(event, ToolProgress):
                yield {
                    "status": "progress",
                    "type": "tool_progress",
                    "tool_call": event.tool_call,
                    "chunk": event.chunk,
                }

        if result_text:
            yield {
                "status": "handoff_complete",
                "type": "handoff_complete",
                "message": "Delegated task completed",
                "result": result_text,
                "target_agent": target_agent_name,
            }
        else:
            yield {
                "status": "handoff_complete",
                "type": "handoff_complete",
                "message": "Delegated task completed",
                "target_agent": target_agent_name,
            }
    finally:
        if sub_stop_event is not None:
            sub_stop_event.set()
        if sub_task is not None:
            sub_task.cancel()
            try:
                await sub_task
            except (asyncio.CancelledError, Exception):
                pass
        await release_session_lock(handoff_session_id, lock)


TRIAGE_SYSTEM_PROMPT = """You are a general assistant that helps users with various tasks. Understand the user's request and either handle it directly or route it to the right specialist agent.

Available specialist agents:
- code-reviewer — Analyzes code changes for bugs, style issues, security vulnerabilities, and performance problems
- writer — Helps craft clear, engaging, and well-structured content including articles, emails, reports, and creative writing

When a user's request matches a specialist's expertise, use the handoff tool to transfer the conversation to them.
For general knowledge questions or information lookup, use the web_search tool to find answers online."""

TRIAGE_SYSTEM_PROMPT_ZH = """你是一个通用助手，帮助用户处理各种任务。理解用户的需求，直接处理或将其路由到合适的专业智能体。

可用的专业智能体：
- code-reviewer — 分析代码变更中的缺陷、风格问题、安全漏洞和性能问题
- writer — 帮助撰写清晰、有吸引力且结构良好的内容，包括文章、邮件、报告和创意写作

当用户的需求符合某个专业智能体的专长时，使用 handoff 工具将对话移交给该智能体。
对于一般的知识问答或信息查询，使用 web_search 工具在线搜索答案。"""

CODE_REVIEW_SYSTEM_PROMPT = """You are a senior code reviewer. Analyze code changes for bugs, style issues, security vulnerabilities, and performance problems. Provide constructive, specific feedback with concrete fix suggestions. Be thorough but concise.
When the user's request is outside your expertise, use handoff to transfer to another agent. Use discover_agents to find available agents first."""

CODE_REVIEW_SYSTEM_PROMPT_ZH = """你是一名资深代码审查专家。分析代码变更中的缺陷、风格问题、安全漏洞和性能问题。给出有建设性的具体反馈，并提供修复建议。做到既全面又简洁。
当用户的需求超出你的专业范围时，使用 handoff 将任务移交给其他智能体。先使用 discover_agents 查找可用智能体。"""

WRITER_SYSTEM_PROMPT = """You are a professional writing assistant. Help users craft clear, engaging, and well-structured content including articles, emails, reports, and creative writing. Provide thoughtful suggestions and improvements.
When the user's request is outside your expertise, use handoff to transfer to another agent. Use discover_agents to find available agents first."""

WRITER_SYSTEM_PROMPT_ZH = """你是一名专业的写作助手。帮助用户撰写清晰、有吸引力且结构良好的内容，包括文章、邮件、报告和创意写作。提供周到的建议和改进方案。
当用户的需求超出你的专业范围时，使用 handoff 将任务移交给其他智能体。先使用 discover_agents 查找可用智能体。"""


BUILTIN_AGENTS: list[dict[str, Any]] = [
    {
        "name": "triage",
        "display_name": "General Assistant",
        "display_name_locale": '{"zh":"通用助手","en":"General Assistant"}',
        "description": "A general-purpose assistant that understands your request and routes it to the right specialist",
        "description_locale": '{"zh":"一个通用助手，理解您的需求并将其路由到合适的专业智能体","en":"A general-purpose assistant that understands your request and routes it to the right specialist"}',
        "system_prompt": TRIAGE_SYSTEM_PROMPT,
        "system_prompt_locale": json.dumps(
            {"zh": TRIAGE_SYSTEM_PROMPT_ZH, "en": TRIAGE_SYSTEM_PROMPT},
            ensure_ascii=False,
        ),
        "provider": "openai",
        "model": "deepseek-v4-flash",
        "agent_type": "compacting",
        "compaction": {
            "prompt_token_threshold": 8000,
            "keep_recent": 6,
        },
    },
    {
        "name": "code-reviewer",
        "display_name": "Code Reviewer",
        "display_name_locale": '{"zh":"代码审查","en":"Code Reviewer"}',
        "description": "Reviews code changes for quality and best practices",
        "description_locale": '{"zh":"审查代码变更，确保质量和最佳实践","en":"Reviews code changes for quality and best practices"}',
        "system_prompt": CODE_REVIEW_SYSTEM_PROMPT,
        "system_prompt_locale": json.dumps(
            {"zh": CODE_REVIEW_SYSTEM_PROMPT_ZH, "en": CODE_REVIEW_SYSTEM_PROMPT},
            ensure_ascii=False,
        ),
        "provider": "openai",
        "model": "deepseek-v4-flash",
    },
    {
        "name": "writer",
        "display_name": "Writing Assistant",
        "display_name_locale": '{"zh":"写作助手","en":"Writing Assistant"}',
        "description": "Helps with writing and content creation tasks",
        "description_locale": '{"zh":"协助完成写作和内容创作任务","en":"Helps with writing and content creation tasks"}',
        "system_prompt": WRITER_SYSTEM_PROMPT,
        "system_prompt_locale": json.dumps(
            {"zh": WRITER_SYSTEM_PROMPT_ZH, "en": WRITER_SYSTEM_PROMPT},
            ensure_ascii=False,
        ),
        "provider": "openai",
        "model": "deepseek-v4-flash",
    },
]

BUILTIN_TOOLS: list[dict[str, Any]] = [
    {
        "name": "calculator",
        "display_name": "Calculator",
        "display_name_locale": '{"zh":"计算器","en":"Calculator"}',
        "description": "Perform arithmetic calculations (addition, subtraction, multiplication, division, exponentiation)",
        "description_locale": '{"zh":"执行算术运算（加、减、乘、除、乘方）","en":"Perform arithmetic calculations (addition, subtraction, multiplication, division, exponentiation)"}',
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Arithmetic expression to evaluate (e.g. 2 + 2)",
                }
            },
            "required": ["expression"],
        },
        "endpoint_url": "http://localhost:8006/api/v1/tools/calculator/execute",
    },
    {
        "name": "web_search",
        "display_name": "Web Search",
        "display_name_locale": '{"zh":"网络搜索","en":"Web Search"}',
        "description": "Search the web for information (simulated)",
        "description_locale": '{"zh":"搜索网络信息（模拟）","en":"Search the web for information (simulated)"}',
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        },
        "_fn": _web_search_fn,
    },
    {
        "name": "handoff",
        "display_name": "Handoff",
        "display_name_locale": '{"zh":"任务移交","en":"Handoff"}',
        "description": "Hand off a task to another agent. Use discover_agents first to find available agents.",
        "description_locale": '{"zh":"将任务移交给其他智能体。先使用 discover_agents 查找可用智能体。","en":"Hand off a task to another agent. Use discover_agents first to find available agents."}',
        "parameters": {
            "type": "object",
            "properties": {
                "target_agent_name": {
                    "type": "string",
                    "description": "The name of the target agent to hand off to.",
                },
                "context_summary": {
                    "type": "string",
                    "description": "Summary of the current context and conversation state.",
                },
                "task_description": {
                    "type": "string",
                    "description": "Description of the task to hand off to the next agent.",
                },
                "locale": {"type": "string", "description": "Language code (zh/en)"},
            },
            "required": ["target_agent_name", "context_summary", "task_description"],
        },
        "_fn": _handoff_fn,
    },
    {
        "name": "discover_agents",
        "display_name": "Discover Agents",
        "display_name_locale": '{"zh":"发现智能体","en":"Discover Agents"}',
        "description": "Discover available agents that can accept handoffs.",
        "description_locale": '{"zh":"发现可以接受任务移交的可用智能体。","en":"Discover available agents that can accept handoffs."}',
        "parameters": {
            "type": "object",
            "properties": {
                "exclude": {
                    "type": "string",
                    "description": "Agent name to exclude from results",
                },
                "locale": {"type": "string", "description": "Language code (zh/en)"},
            },
        },
        "_fn": _discover_agents_fn,
    },
    {
        "name": "show_ui_meta",
        "display_name": "Show UI Meta",
        "display_name_locale": '{"zh":"展示UI元数据","en":"Show UI Meta"}',
        "description": "Display information in an optimized visual format",
        "description_locale": '{"zh":"以优化的视觉布局展示信息","en":"Display information in an optimized visual format"}',
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query for profiles",
                }
            },
        },
        "endpoint_url": "http://localhost:8006/api/v1/tools/show_ui_meta/execute",
    },
    {
        "name": "stop_agent",
        "display_name": "Stop Agent",
        "display_name_locale": '{"zh":"停止执行","en":"Stop Agent"}',
        "description": "Stop the agent execution loop after receiving the tool result",
        "description_locale": '{"zh":"在收到工具执行结果后停止agent循环","en":"Stop the agent execution loop after receiving the tool result"}',
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The final message to return to the user",
                }
            },
            "required": ["message"],
        },
        "endpoint_url": "http://localhost:8006/api/v1/tools/stop_agent/execute",
    },
]

BUILTIN_SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "triage",
        "name": "General Assistant",
        "name_locale": '{"zh":"通用助手","en":"General Assistant"}',
        "icon": "\u2728",
        "description": "Chat with a general-purpose assistant",
        "description_locale": '{"zh":"与通用助手聊天","en":"Chat with a general-purpose assistant"}',
        "agents": [
            {
                "name": "triage",
                "tool_names": [
                    "handoff",
                    "discover_agents",
                    "calculator",
                    "web_search",
                    "show_ui_meta",
                    "stop_agent",
                ],
            },
            {
                "name": "code-reviewer",
                "tool_names": [
                    "handoff",
                    "discover_agents",
                    "stop_agent",
                ],
            },
            {
                "name": "writer",
                "tool_names": [
                    "handoff",
                    "discover_agents",
                    "web_search",
                    "stop_agent",
                ],
            },
        ],
    },
    {
        "id": "code_review",
        "name": "Code Review",
        "name_locale": '{"zh":"代码审查","en":"Code Review"}',
        "icon": "\U0001f4bb",
        "description": "Review code changes",
        "description_locale": '{"zh":"审查代码变更","en":"Review code changes"}',
        "agents": [
            {
                "name": "code-reviewer",
                "tool_names": ["handoff", "discover_agents", "stop_agent"],
            }
        ],
    },
    {
        "id": "writing",
        "name": "Writing Assistant",
        "name_locale": '{"zh":"写作助手","en":"Writing Assistant"}',
        "icon": "\U0001f4dd",
        "description": "Help with writing",
        "description_locale": '{"zh":"协助写作","en":"Help with writing"}',
        "agents": [
            {
                "name": "writer",
                "tool_names": [
                    "handoff",
                    "discover_agents",
                    "web_search",
                    "stop_agent",
                ],
            }
        ],
    },
]
