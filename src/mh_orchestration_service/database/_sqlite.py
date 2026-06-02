from __future__ import annotations

import json
from typing import Any, cast
from uuid import uuid4

from minimal_harness.database import generate_bigint_id
from minimal_harness.memory import Memory, Message
from minimal_harness.memory_store import SessionStoreProtocol
from minimal_harness.session import Session, SessionSummary, SimpleSession

from mh_orchestration_service.database._protocol import (
    DatabaseProtocol,
    _ts_ms,
)

SYSTEM_USER_ID = 0


class SqliteDatabase:
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

    # ── Transaction support ──

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

    # ── Schema initialisation ──

    async def init_schema(self) -> None:
        try:
            await self.fetch_one("SELECT creation_date FROM sessions LIMIT 1")
        except Exception:
            await self.execute("DROP TABLE IF EXISTS session_messages")
            await self.execute("DROP TABLE IF EXISTS sessions")

        await self.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                id BIGINT PRIMARY KEY,
                session_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                scenario_id TEXT NOT NULL,
                title TEXT DEFAULT '',
                status TEXT DEFAULT 'idle',
                created_by BIGINT NOT NULL,
                last_updated_by BIGINT NOT NULL,
                creation_date TIMESTAMP NOT NULL,
                last_update_date TIMESTAMP NOT NULL,
                delete_flag TEXT DEFAULT 'N',
                last_update_trace_id TEXT NOT NULL,
                transient TEXT DEFAULT 'N',
                agent_display_name_locale TEXT DEFAULT ''
            )"""
        )
        await self.execute(
            """CREATE TABLE IF NOT EXISTS session_messages (
                id BIGINT PRIMARY KEY,
                session_id TEXT NOT NULL,
                data TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_by BIGINT NOT NULL,
                last_updated_by BIGINT NOT NULL,
                creation_date TIMESTAMP NOT NULL,
                last_update_date TIMESTAMP NOT NULL,
                delete_flag TEXT DEFAULT 'N',
                last_update_trace_id TEXT NOT NULL
            )"""
        )
        await self.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions(session_id)"
        )
        await self.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_messages_session_id ON session_messages(session_id)"
        )

        # Migrate existing tables that lack the transient column
        try:
            await self.fetch_one("SELECT transient FROM sessions LIMIT 1")
        except Exception:
            await self.execute(
                "ALTER TABLE sessions ADD COLUMN transient TEXT DEFAULT 'N'"
            )

        # Migrate existing session_messages that lack the sort_order column
        try:
            await self.fetch_one("SELECT sort_order FROM session_messages LIMIT 1")
        except Exception:
            await self.execute(
                "ALTER TABLE session_messages ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"
            )

        # Migrate existing sessions that lack the agent_display_name_locale column
        try:
            await self.fetch_one(
                "SELECT agent_display_name_locale FROM sessions LIMIT 1"
            )
        except Exception:
            await self.execute(
                "ALTER TABLE sessions ADD COLUMN agent_display_name_locale TEXT DEFAULT ''"
            )

        await self.execute_write("SELECT 1")

    # ── Session store ──

    async def create_session_store(self) -> SessionStoreProtocol:
        return _SqliteSessionStore(self)


class _SqliteSessionStore:
    def __init__(self, db: DatabaseProtocol) -> None:
        self._db = db
        self._cache: dict[str, SimpleSession] = {}

    async def create_session(
        self,
        session_id: str | None = None,
        agent_name: str = "",
        user_id: str = "",
        scenario_id: str | None = None,
        transient: bool = False,
        display_name_locale: str | None = None,
    ) -> SimpleSession:
        sid = session_id or f"mem_{uuid4().hex[:12]}"
        now = _ts_ms()
        trace_id = uuid4().hex

        audit_user_id = int(user_id)

        session = SimpleSession(
            session_id=sid,
            agent_name=agent_name,
            user_id=user_id,
            scenario_id=scenario_id,
            display_name_locale=display_name_locale,
        )

        await self._db.execute_write(
            """INSERT INTO sessions
               (id, session_id, user_id, agent_name, scenario_id, status,
                created_by, last_updated_by, creation_date, last_update_date,
                delete_flag, last_update_trace_id, transient, agent_display_name_locale)
               VALUES (?, ?, ?, ?, ?, 'idle',
                       ?, ?, ?, ?,
                       'N', ?, ?, ?)""",
            [
                session.db_id,
                sid,
                user_id,
                agent_name,
                scenario_id or "",
                audit_user_id,
                audit_user_id,
                now,
                now,
                trace_id,
                "Y" if transient else "N",
                display_name_locale or "",
            ],
        )

        session.created_at = now
        self._cache[sid] = session
        return session

    async def get_session(self, session_id: str) -> SimpleSession | None:
        if session_id in self._cache:
            return self._cache[session_id]

        row = await self._db.fetch_one(
            "SELECT * FROM sessions WHERE session_id = ? AND delete_flag = 'N'",
            [session_id],
        )
        if row is None:
            return None

        session = SimpleSession(
            session_id=row["session_id"],
            agent_name=row["agent_name"],
            user_id=row["user_id"],
            scenario_id=row["scenario_id"],
            display_name_locale=row.get("agent_display_name_locale"),
        )
        session.db_id = row["id"]
        session.created_at = row["creation_date"]
        session.title = row.get("title")

        msg_rows = await self._db.fetch_all(
            "SELECT data FROM session_messages WHERE session_id = ? AND delete_flag = 'N' ORDER BY sort_order, id",
            [session_id],
        )
        for m in msg_rows:
            msg_data = json.loads(m["data"])
            if isinstance(msg_data, dict):
                await session.add_message(cast("Message", msg_data))

        session.memory.set_persisted_count(len(msg_rows))

        self._cache[session_id] = session
        return session

    async def save_memory(
        self, memory: Memory, session_id: str, extra: dict[str, Any] | None = None
    ) -> None:
        now = _ts_ms()
        trace_id = uuid4().hex
        new_msgs = memory.get_new_messages()
        title = (extra or {}).get("title")

        if not new_msgs and not title:
            return

        session_row = await self._db.fetch_one(
            "SELECT user_id, created_by FROM sessions WHERE session_id = ? AND delete_flag = 'N'",
            [session_id],
        )
        audit_id = session_row["created_by"] if session_row else SYSTEM_USER_ID

        base_order = memory.get_persisted_count()

        await self._db.begin()
        try:
            if new_msgs:
                rows = []
                for idx, m in enumerate(new_msgs):
                    mid = generate_bigint_id()
                    rows.append(
                        [
                            mid,
                            session_id,
                            json.dumps(m, ensure_ascii=False),
                            base_order + idx,
                            audit_id,
                            audit_id,
                            now,
                            now,
                            "N",
                            trace_id,
                        ]
                    )
                await self._db.executemany(
                    """INSERT INTO session_messages
                       (id, session_id, data, sort_order,
                        created_by, last_updated_by, creation_date, last_update_date,
                        delete_flag, last_update_trace_id)
                       VALUES (?, ?, ?, ?,
                               ?, ?, ?, ?,
                               ?, ?)""",
                    rows,
                )

            if title:
                await self._db.execute(
                    "UPDATE sessions SET title = ?, last_updated_by = ?, last_update_date = ?, status = 'idle', last_update_trace_id = ? WHERE session_id = ?",
                    [title, audit_id, now, trace_id, session_id],
                )
            else:
                await self._db.execute(
                    "UPDATE sessions SET last_updated_by = ?, last_update_date = ?, status = 'idle', last_update_trace_id = ? WHERE session_id = ?",
                    [audit_id, now, trace_id, session_id],
                )

            await self._db.commit()
        except Exception:
            await self._db.rollback()
            raise

        if new_msgs:
            memory.mark_all_persisted()

    async def delete_session(self, session_id: str) -> bool:
        self._cache.pop(session_id, None)
        now = _ts_ms()
        trace_id = uuid4().hex

        await self._db.execute_write(
            "UPDATE session_messages SET delete_flag = 'Y', last_updated_by = ?, last_update_date = ?, last_update_trace_id = ? WHERE session_id = ?",
            [SYSTEM_USER_ID, now, trace_id, session_id],
        )
        cur = await self._db.execute(
            "UPDATE sessions SET delete_flag = 'Y', last_updated_by = ?, last_update_date = ?, last_update_trace_id = ?, status = 'deleted' WHERE session_id = ? AND delete_flag = 'N'",
            [SYSTEM_USER_ID, now, trace_id, session_id],
        )
        return cur.rowcount > 0

    async def list_sessions(self) -> list[SessionSummary]:
        rows = await self._db.fetch_all(
            """SELECT s.session_id, s.agent_name, s.user_id, s.scenario_id, s.title, s.creation_date, s.status, s.agent_display_name_locale,
                      (SELECT COUNT(*) FROM session_messages m WHERE m.session_id = s.session_id AND m.delete_flag = 'N') AS message_count
               FROM sessions s
               WHERE s.delete_flag = 'N' AND s.transient = 'N'
               ORDER BY s.creation_date DESC"""
        )
        result: list[SessionSummary] = []
        for r in rows:
            result.append(
                SessionSummary(
                    session_id=r["session_id"],
                    agent_name=r["agent_name"],
                    user_id=r["user_id"],
                    scenario_id=r["scenario_id"],
                    title=r.get("title"),
                    created_at=r["creation_date"],
                    message_count=r["message_count"],
                    status=r["status"],
                    display_name_locale=r.get("agent_display_name_locale"),
                )
            )
        return result

    async def list_user_sessions(
        self, user_id: str, scenario_id: str | None = None
    ) -> list[SessionSummary]:
        if scenario_id:
            rows = await self._db.fetch_all(
                """SELECT s.session_id, s.agent_name, s.user_id, s.scenario_id, s.title, s.creation_date, s.status, s.agent_display_name_locale,
                          (SELECT COUNT(*) FROM session_messages m WHERE m.session_id = s.session_id AND m.delete_flag = 'N') AS message_count
                   FROM sessions s
                   WHERE s.user_id = ? AND s.scenario_id = ? AND s.delete_flag = 'N' AND s.transient = 'N'
                   ORDER BY s.creation_date DESC""",
                [user_id, scenario_id],
            )
        else:
            rows = await self._db.fetch_all(
                """SELECT s.session_id, s.agent_name, s.user_id, s.scenario_id, s.title, s.creation_date, s.status, s.agent_display_name_locale,
                          (SELECT COUNT(*) FROM session_messages m WHERE m.session_id = s.session_id AND m.delete_flag = 'N') AS message_count
                   FROM sessions s
                   WHERE s.user_id = ? AND s.delete_flag = 'N' AND s.transient = 'N'
                   ORDER BY s.creation_date DESC""",
                [user_id],
            )
        result: list[SessionSummary] = []
        for r in rows:
            result.append(
                SessionSummary(
                    session_id=r["session_id"],
                    agent_name=r["agent_name"],
                    user_id=r["user_id"],
                    scenario_id=r["scenario_id"],
                    title=r.get("title"),
                    created_at=r["creation_date"],
                    message_count=r["message_count"],
                    status=r["status"],
                    display_name_locale=r.get("agent_display_name_locale"),
                )
            )
        return result

    async def get_session_messages(self, session_id: str) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT data FROM session_messages WHERE session_id = ? AND delete_flag = 'N' ORDER BY sort_order, id",
            [session_id],
        )
        return [json.loads(r["data"]) for r in rows]

    def get_messages_as_items(self, session: Session) -> list[dict]:
        items: list[dict] = []
        for i, msg in enumerate(session.get_all_messages()):
            role = msg.get("role", "")
            content = msg.get("content")
            if content is None:
                content = None
            elif isinstance(content, list):
                texts = [
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                content = "\n".join(texts)
            elif not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            items.append(
                {
                    "id": f"msg-{i}",
                    "role": role,
                    "content": content,
                    "tool_calls": msg.get("tool_calls"),
                    "tool_call_id": msg.get("tool_call_id"),
                    "progress": msg.get("progress"),
                    "meta": msg.get("meta"),
                }
            )
        return items
