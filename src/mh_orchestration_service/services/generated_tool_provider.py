from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Protocol, runtime_checkable

from minimal_harness.llm.llm import LLMProvider
from minimal_harness.memory import system_message, user_message

logger = logging.getLogger("orchestration.generated_tool_provider")


@dataclass
class GeneratedToolMeta:
    name: str
    display_name: str
    description: str
    parameters: dict[str, Any]
    source_code: str
    user_id: str = ""
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        now = datetime.now(UTC).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now


@runtime_checkable
class ToolGenerator(Protocol):
    """Pure tool generation — no storage.

    Generates tool metadata + source code from a natural language
    description. Persistence is handled by the caller via
    ``MetadataManager``.
    """

    def generate_stream(
        self, natural_description: str
    ) -> AsyncGenerator[dict[str, Any], None]: ...


def tool_to_dict(t: GeneratedToolMeta) -> dict[str, Any]:
    return {
        "name": t.name,
        "display_name": t.display_name,
        "description": t.description,
        "parameters": t.parameters,
        "source_code": t.source_code,
        "user_id": t.user_id,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }


_GENERATION_SYSTEM_PROMPT = """\
You are a Python tool developer. Given a user's description, output a JSON object with the tool's metadata AND its implementation logic.

Output format:
{
  "name": "snake_case_unique_identifier",
  "display_name": "Short Human-Readable Name",
  "description": "Clear description of what this tool does",
  "parameters": { JSON Schema for input parameters },
  "source_code": "Python implementation — ONLY the async function body, no metadata"
}

CRITICAL rules for source_code:
1. Write ONLY the function body. Do NOT include register, decorators, or metadata.
2. The function MUST be named `run` and MUST be an async generator: `async def run(**kwargs):`
3. Yield strings for progress messages: `yield "Processing step 1..."`
4. Yield a dict as the FINAL result: `yield {"output": 42, "status": "ok"}`
5. NEVER use `return <value>` — it will crash. Use `yield <dict>` for the final output.
6. Use ONLY Python standard library. Network access is allowed (socket, http, urllib, ssl). The following modules are BLOCKED and must NOT be used:
   - System: os, subprocess, shutil, signal, pty, fcntl, mmap
   - Filesystem: pathlib, glob, fnmatch, tempfile, fileinput, filecmp
   - Archive: zipfile, tarfile, gzip, bz2, lzma
   - Code execution: importlib, pdb, code, codeop, compileall
   - Concurrency: threading, concurrent.futures
   - Serialization: pickle, marshal
   - Other: ctypes, multiprocessing, webbrowser, crypt, grp, pwd, linecache
7. Keep it simple and focused on the logic.

Example of BAD code (will crash):
```python
async def run(**kwargs):
    yield "working..."
    return {"result": 42}  # WRONG — async generator cannot return a value
```

Example of GOOD code:
```python
async def run(**kwargs):
    text = kwargs.get("text", "")
    yield "Converting text to uppercase..."
    result = text.upper()
    yield {"original": text, "uppercase": result}
```
"""


class DefaultToolGenerator:
    """Pure LLM-based tool generator — no storage.

    Generates ``GeneratedToolMeta`` from a natural-language description.
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
        self, natural_description: str
    ) -> AsyncGenerator[dict[str, Any], None]:
        if self._llm_factory is None:
            yield {
                "type": "error",
                "data": {"message": "LLM factory not configured for tool generation"},
            }
            return

        yield {
            "type": "generating",
            "data": {"phase": "start", "message": "Starting generation..."},
        }

        llm = self._llm_factory()
        messages = [
            system_message(_GENERATION_SYSTEM_PROMPT),
            user_message(
                [{"type": "text", "text": f"Create a tool for: {natural_description}"}]
            ),
        ]

        stream = await llm.chat(
            messages=messages, tools=[], temperature=0.3, max_tokens=4096
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
            data: dict[str, Any] = json.loads(json_str)  # type: ignore[assignment]
        except json.JSONDecodeError:
            logger.error("tool.generated.parse_failed content=%s", raw[:500])
            yield {
                "type": "error",
                "data": {"message": "Failed to parse LLM output as JSON"},
            }
            return

        tool = GeneratedToolMeta(
            name=data.get("name", ""),
            display_name=data.get("display_name", ""),
            description=data.get("description", ""),
            parameters=data.get("parameters", {}),
            source_code=data.get("source_code", ""),
        )

        yield {"type": "generated", "data": tool_to_dict(tool)}
