from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator


async def _web_search_fn(query: str = "") -> AsyncIterator[Any]:
    await asyncio.sleep(0.05)
    yield f'[Simulated] Search results for "{query}": Found 42 relevant pages. Top result: example.com/{query.replace(" ", "-")}'


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
        "endpoint_url": "/api/v1/tools/calculator/execute",
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
        "endpoint_url": "/api/v1/tools/handoff/execute",
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
        "endpoint_url": "/api/v1/tools/discover_agents/execute",
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
        "endpoint_url": "/api/v1/tools/show_ui_meta/execute",
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
        },
        "endpoint_url": "/api/v1/tools/stop_agent/execute",
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
