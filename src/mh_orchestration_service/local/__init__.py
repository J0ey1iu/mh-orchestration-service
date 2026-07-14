"""Pure-logic helpers shared between orchestration-service and external clients.

This sub-package is the **library surface** of orchestration-service: it
contains zero-dependency functions that any consumer (the orch-service
HTTP layer, the TUI, the agent-tool-service, third-party SDKs) can
import directly.  No FastAPI, no Pydantic-FastAPI, no database drivers.

Adding to this package MUST NOT introduce a dependency on:

- ``fastapi``
- ``pydantic`` (the FastAPI-coupled build)
- any DB driver (aiosqlite, asyncpg, etc.)
- any other layer-3 application code (mh_tui, mh-service-kit runtime)

Allowed dependencies: the Python stdlib, ``mh-orchestration-service``
internal *types* (e.g. ``UserIdentity``), and ``minimal-harness.types``.
"""
