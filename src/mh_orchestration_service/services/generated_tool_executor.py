from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, AsyncIterator

logger = logging.getLogger("orchestration.generated_tool_executor")


async def execute_generated_tool(
    source_code: str,
    args: dict[str, Any],
    tool_name: str,
    *,
    timeout: float = 60.0,
) -> AsyncIterator[Any]:
    runner_code = _build_runner(source_code, args, tool_name)

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        runner_code,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        assert proc.stdout is not None
        while True:
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                yield {"error": f"Tool execution timed out after {timeout}s"}
                return
            if not line:
                break
            decoded = line.decode("utf-8").strip()
            if decoded:
                try:
                    yield json.loads(decoded)
                except json.JSONDecodeError:
                    yield decoded
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()

    if proc.returncode != 0:
        stderr_data = b""
        if proc.stderr is not None:
            stderr_data = await proc.stderr.read()
        stderr = stderr_data.decode("utf-8", errors="replace") if stderr_data else ""
        logger.error(
            "tool.generated.subprocess.error name=%s code=%d stderr=%s",
            tool_name,
            proc.returncode,
            stderr[:500],
        )
        yield {
            "error": f"Tool execution failed (exit {proc.returncode}): {stderr[:1000]}"
        }


def _build_runner(source_code: str, args: dict[str, Any], tool_name: str) -> str:
    args_json = json.dumps(args, default=str)
    source_json = json.dumps(source_code)

    return f"""\
import builtins as _builtins
import sys as _sys, json as _json, asyncio as _asyncio, traceback as _traceback, inspect as _inspect

_SAFE_BUILTINS = {{k: v for k, v in _builtins.__dict__.items() if k not in {{
    'open', 'eval', 'exec', 'compile', 'breakpoint', 'input',
    'getattr', 'hasattr', 'type', 'vars', '__build_class__',
}}}}

_BLOCKED_MODULES = {{
    'os', 'subprocess', 'shutil',
    'pathlib', 'glob', 'fnmatch', 'tempfile', 'fileinput', 'filecmp',
    'pickle', 'ctypes', 'multiprocessing', 'signal',
    'importlib', 'pdb', 'code', 'codeop', 'compileall',
    'webbrowser', 'pty', 'fcntl', 'mmap', 'crypt', 'grp', 'pwd',
    'zipfile', 'tarfile', 'gzip', 'bz2', 'lzma',
    'concurrent.futures', 'threading',
    'marshal', 'linecache',
}}

def _safe_import(name, g=None, l=None, fl=(), lv=0):
    _top = name.split('.')[0]
    if _top in _BLOCKED_MODULES:
        raise ImportError(f"Module '{{name}}' is not allowed in generated tools")
    return _builtins.__import__(name, g, l, fl, lv)

_SAFE_BUILTINS['__import__'] = _safe_import

SOURCE = {source_json}
ARGS = _json.loads({json.dumps(args_json)})
TOOL_NAME = {json.dumps(tool_name)}

_RESTRICTED = {{
    '__builtins__': _SAFE_BUILTINS,
    '__import__': _safe_import,
    'json': _json,
    'inspect': _inspect,
}}

try:
    exec(compile(SOURCE, "<generated_tool>", "exec"), _RESTRICTED)
except Exception as e:
    print(_json.dumps({{"error": "Compile error: " + str(e), "traceback": _traceback.format_exc()}}), flush=True)
    _sys.exit(1)

_fn = _RESTRICTED.get("run")
if _fn is None:
    for _k, _v in _RESTRICTED.items():
        if callable(_v) and _inspect.iscoroutinefunction(_v) and not _k.startswith("_"):
            if hasattr(_inspect, "isasyncgenfunction") and _inspect.isasyncgenfunction(_v):
                _fn = _v
                break

if _fn is None:
    print(_json.dumps({{"error": "No async function named 'run' found in generated code"}}), flush=True)
    _sys.exit(1)

try:
    result = _fn(**ARGS)
except Exception as e:
    print(_json.dumps({{"error": str(e), "traceback": _traceback.format_exc()}}), flush=True)
    _sys.exit(1)

async def _consume():
    try:
        if _inspect.isasyncgen(result):
            async for chunk in result:
                print(_json.dumps(chunk, default=str), flush=True)
        elif _asyncio.iscoroutine(result):
            val = await result
            print(_json.dumps(val, default=str), flush=True)
        elif _inspect.isgenerator(result):
            for chunk in result:
                print(_json.dumps(chunk, default=str), flush=True)
        else:
            print(_json.dumps(result, default=str), flush=True)
    except Exception as e:
        print(_json.dumps({{"error": str(e), "traceback": _traceback.format_exc()}}), flush=True)

_asyncio.run(_consume())
"""
