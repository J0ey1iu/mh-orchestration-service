from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from mh_orchestration_service.database._memory_store import SessionStoreProtocol

from mh_orchestration_service.database import (
    BuiltinSessionStore,
    DatabaseProtocol,
    SqliteDatabase,
)

_db: DatabaseProtocol | None = None
_session_store_factory: (
    Callable[[], Awaitable[SessionStoreProtocol]]
    | Callable[[], SessionStoreProtocol]
    | None
) = None


def set_db(provider: DatabaseProtocol) -> None:
    global _db
    _db = provider


def get_db() -> DatabaseProtocol:
    if _db is None:
        raise RuntimeError(
            "Database not initialized. Did you call init_db() in lifespan?"
        )
    return _db


def set_session_store_factory(
    factory: (
        Callable[[], Awaitable[SessionStoreProtocol]]
        | Callable[[], SessionStoreProtocol]
    ),
) -> None:
    global _session_store_factory
    _session_store_factory = factory


async def get_session_store() -> SessionStoreProtocol:
    if _session_store_factory is not None:
        result = _session_store_factory()
        if inspect.isawaitable(result):
            return await result
        return result
    return BuiltinSessionStore(get_db())


async def init_db(dsn: str, auto_schema: bool = True) -> None:
    d = SqliteDatabase()
    set_db(d)
    await d.init(dsn)
    if auto_schema:
        store = BuiltinSessionStore(d)
        await store.init_schema()
