"""Shared SSE helper for all orchestration-service endpoints.

Historically each SSE-emitting route built its own ``data: {...}\\n\\n``
or ``event: X\\ndata: Y\\n\\n`` line, with subtle differences.  The
2026-07-14 audit (Theme A) flagged this as a maintainability hazard:
``chat.py`` used the ``event:/data:`` form, while
``runtime_tools.py`` / ``tool_generator.py`` / ``agent_generator.py``
all used the ``{"type", "data"}`` JSON envelope.

This module is the single source of truth.  Every SSE endpoint in
``mh_orchestration_service.api`` should import :func:`sse_event` (or
:func:`sse_envelope`) and use the chosen envelope everywhere.

Envelope shape (one SSE line)::

    data: {"type": "AgentStart", "data": {...}}\\n\\n

That is, the same ``{"type", "data"}`` JSON envelope the four
non-chat endpoints already used.  Frontends parse a single shape and
dispatch on ``type``.

.. note::

    Changing this envelope is a breaking change for any consumer
    that parsed the old ``event:`` / ``data:`` shape (notably the
    web-frontend's chat view).  The migration note for downstream
    consumers lives in ``docs/AUDIT_2026-07-14.md`` § P10.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = [
    "sse_envelope",
    "sse_event",
]


def sse_envelope(event_type: str, data: Any) -> str:
    """Return a single SSE line of the canonical ``{"type","data"}`` envelope.

    The line is terminated with a blank line (``\\n\\n``) so the
    browser EventSource / curl both see a complete frame.
    """
    return f"data: {json.dumps({'type': event_type, 'data': data}, ensure_ascii=False, default=str)}\n\n"


# Backwards-compatible alias used by the previous ``_sse_line`` /
# ``_format_sse`` call sites; emits the same envelope.
sse_event = sse_envelope
