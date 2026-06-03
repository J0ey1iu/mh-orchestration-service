from __future__ import annotations

from typing import Any

from mh_orchestration_service.database._protocol import DatabaseProtocol


class SqliteDatabase(DatabaseProtocol):
    def __init__(self) -> None:
        self._conn: Any = None

    async def init(self, dsn: str) -> None:
        import aiosqlite

        self._conn = await aiosqlite.connect(dsn)
        self._conn.row_factory = aiosqlite.Row

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def execute(self, sql: str, params: list | None = None) -> Any:
        assert self._conn is not None
        return await self._conn.execute(sql, params or [])

    async def execute_write(self, sql: str, params: list | None = None) -> int:
        cursor = await self.execute(sql, params)
        assert self._conn is not None
        await self._conn.commit()
        return cursor.lastrowid or 0

    async def execute_many_write(self, sql: str, params_list: list[list]) -> None:
        assert self._conn is not None
        await self._conn.executemany(sql, params_list)
        await self._conn.commit()

    async def fetch_one(self, sql: str, params: list | None = None) -> dict | None:
        cursor = await self.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def fetch_all(self, sql: str, params: list | None = None) -> list[dict]:
        cursor = await self.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def begin(self) -> None:
        assert self._conn is not None
        await self._conn.execute("BEGIN IMMEDIATE")

    async def commit(self) -> None:
        assert self._conn is not None
        await self._conn.commit()

    async def rollback(self) -> None:
        assert self._conn is not None
        await self._conn.rollback()

    async def executemany(self, sql: str, params_list: list[list]) -> None:
        assert self._conn is not None
        await self._conn.executemany(sql, params_list)
