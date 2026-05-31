from __future__ import annotations

from minimal_harness.database import DatabaseBackend, DatabaseProtocol
from minimal_harness.memory_store import SessionStoreProtocol

_db: DatabaseProtocol | None = None
_db_type: str = ""


def get_db() -> DatabaseProtocol:
    global _db
    if _db is None:
        raise RuntimeError(
            "Database not initialized. Did you call init_db() in lifespan?"
        )
    return _db


def get_db_type() -> str:
    return _db_type


def set_db(provider: DatabaseProtocol, db_type: str = "") -> None:
    global _db, _db_type
    _db = provider
    _db_type = db_type


async def init_db(dsn: str, db_type: str, auto_schema: bool = True) -> None:
    global _db_type
    _db_type = db_type
    backend_cls = DatabaseBackend.get(db_type)
    d = backend_cls()  # type: ignore[return-value]
    set_db(d, db_type)
    await d.init(dsn)
    if auto_schema:
        await d.init_schema()


async def get_session_store() -> SessionStoreProtocol:
    return await get_db().create_session_store()
