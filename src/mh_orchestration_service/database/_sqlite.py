from __future__ import annotations

import asyncio
import logging
from typing import Any

from mh_orchestration_service.database._protocol import DatabaseProtocol

logger = logging.getLogger(__name__)


class SqliteDatabase(DatabaseProtocol):
    """Per-task aiosqlite connection pool with WAL + busy_timeout.

    The previous implementation held a single module-global connection
    that aiosqlite serialised through one background thread. Under
    concurrent chat load every event-loop coroutine queued behind that
    one connection. We now keep a small pool (default size = 2× CPU
    count) and acquire / release per logical operation. Each pooled
    connection opens with ``PRAGMA journal_mode=WAL`` and
    ``PRAGMA busy_timeout=5000`` so a transient write lock contention
    waits up to 5 s instead of failing immediately.
    """

    _POOL_SIZE_DEFAULT = 8

    def __init__(self, pool_size: int | None = None) -> None:
        self._dsn: str | None = None
        self._pool_size = pool_size or self._POOL_SIZE_DEFAULT
        self._pool: asyncio.Queue[Any] | None = None
        self._lock = asyncio.Lock()

    async def init(self, dsn: str) -> None:
        import aiosqlite

        self._dsn = dsn
        self._pool = asyncio.Queue(maxsize=self._pool_size)
        # Pre-populate the pool so the first N requests don't pay the
        # connect cost. If a connect fails the pool stays half-warm
        # and the next acquire opens fresh.
        for _ in range(self._pool_size):
            try:
                conn = await self._open_connection(dsn, aiosqlite)
                self._pool.put_nowait(conn)
            except Exception:
                logger.exception("sqlite.pool.warmup.error dsn=%s", dsn)
                break

    async def _open_connection(self, dsn: str, aiosqlite: Any) -> Any:
        conn = await aiosqlite.connect(dsn)
        conn.row_factory = aiosqlite.Row
        # WAL mode gives concurrent readers + a single writer with
        # cooperative locking. busy_timeout makes the writer wait
        # instead of immediately raising "database is locked".
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    async def _acquire(self) -> Any:
        import aiosqlite

        assert self._pool is not None and self._dsn is not None
        try:
            return self._pool.get_nowait()
        except asyncio.QueueEmpty:
            # Pool exhausted; open a fresh connection. aiosqlite's
            # background thread will serialise it.
            return await self._open_connection(self._dsn, aiosqlite)

    async def _release(self, conn: Any) -> None:
        assert self._pool is not None
        try:
            self._pool.put_nowait(conn)
        except asyncio.QueueFull:
            # Pool is at capacity; close this connection to avoid a leak.
            try:
                await conn.close()
            except Exception:
                pass

    async def _check_stale(self, conn: Any) -> bool:
        """Return True if *conn* looks closed or broken.

        aiosqlite's ``Connection`` doesn't expose a clean liveness
        probe; we issue a cheap ``SELECT 1`` and let any failure
        surface. A closed connection raises ``sqlite3.ProgrammingError``;
        any other failure is treated as a stale connection too.
        """
        try:
            await conn.execute("SELECT 1")
            return False
        except Exception:
            return True

    async def _conn(self) -> Any:
        conn = await self._acquire()
        if await self._check_stale(conn):
            try:
                await conn.close()
            except Exception:
                pass
            return await self._acquire()
        return conn

    async def close(self) -> None:
        if self._pool is None:
            return
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                await conn.close()
            except Exception:
                pass

    async def execute(self, sql: str, params: list | None = None) -> Any:
        conn = await self._conn()
        try:
            return await conn.execute(sql, params or [])
        finally:
            await self._release(conn)

    async def execute_write(self, sql: str, params: list | None = None) -> int:
        conn = await self._conn()
        try:
            cursor = await conn.execute(sql, params or [])
            await conn.commit()
            return cursor.lastrowid or 0
        finally:
            await self._release(conn)

    async def fetch_one(self, sql: str, params: list | None = None) -> dict | None:
        conn = await self._conn()
        try:
            cursor = await conn.execute(sql, params or [])
            row = await cursor.fetchone()
            return dict(row) if row else None
        finally:
            await self._release(conn)

    async def fetch_all(self, sql: str, params: list | None = None) -> list[dict]:
        conn = await self._conn()
        try:
            cursor = await conn.execute(sql, params or [])
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        finally:
            await self._release(conn)

    async def executemany(self, sql: str, params_list: list[list]) -> None:
        conn = await self._conn()
        try:
            await conn.executemany(sql, params_list)
            await conn.commit()
        finally:
            await self._release(conn)

    async def transaction(self):
        """Async context manager that holds a single connection across
        ``begin``/``commit``/``rollback`` semantics.

        Use this for any code path that calls ``begin()`` followed by
        multiple writes and a ``commit()`` — the underlying SQLite
        transaction state lives on the connection, not the database
        handle, so a connection-pool implementation must pin the
        connection for the lifetime of the transaction.
        """
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _ctx():
            conn = await self._acquire()
            try:
                await conn.execute("BEGIN IMMEDIATE")
                yield conn
                await conn.commit()
            except Exception:
                try:
                    await conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                await self._release(conn)

        return _ctx()

    async def begin(self) -> None:
        """Deprecated: prefer ``async with db.transaction(): ...``.

        Kept for callers that manage transactions across separate
        calls; opens a fresh connection and immediately begins a
        transaction. Pair with :meth:`commit` / :meth:`rollback` on
        the same logical transaction — but note this can no longer
        guarantee that the operations in between hit the same
        connection, so use :meth:`transaction` instead.
        """
        conn = await self._conn()
        try:
            await conn.execute("BEGIN IMMEDIATE")
        finally:
            await self._release(conn)

    async def commit(self) -> None:
        """Deprecated: see :meth:`begin`."""
        conn = await self._conn()
        try:
            await conn.commit()
        finally:
            await self._release(conn)

    async def rollback(self) -> None:
        """Deprecated: see :meth:`begin`."""
        conn = await self._conn()
        try:
            await conn.rollback()
        finally:
            await self._release(conn)
