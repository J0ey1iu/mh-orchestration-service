from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Protocol, runtime_checkable

from mh_orchestration_service.eval.types import (
    BatchEvalRequest,
    BatchSummary,
    EvalQuestion,
    LLMCallRecord,
    QuestionResult,
)


@runtime_checkable
class EvalResultStorage(Protocol):
    """评测结果存储适配器 —— 客户自行实现。

    所有方法在相应事件发生时即被调用，确保数据增量写入，
    即使中途崩溃也不会丢失已完成的 question 结果。
    """

    async def on_batch_started(
        self, batch_id: str, request: BatchEvalRequest
    ) -> None: ...

    async def on_question_started(
        self, batch_id: str, question: EvalQuestion
    ) -> None: ...

    async def on_llm_call_recorded(
        self, batch_id: str, record: LLMCallRecord
    ) -> None: ...

    async def on_question_completed(
        self, batch_id: str, result: QuestionResult
    ) -> None: ...

    async def on_batch_completed(
        self, batch_id: str, summary: BatchSummary
    ) -> None: ...

    async def get_batch(self, batch_id: str) -> BatchSummary | None: ...

    async def list_batches(self) -> list[BatchSummary]: ...


class LocalFileEvalStorage:
    """本地文件系统实现的 EvalResultStorage。

    目录结构::

        {base_dir}/
            {batch_id}/
                batch.json              ← BatchSummary（增量更新）
                request.json            ← BatchEvalRequest（不变）
                questions/
                    {question_id}/
                        question.json   ← QuestionResult（增量更新）
                        llm_calls/
                            {call_id}.json  ← LLMCallRecord
    """

    def __init__(self, base_dir: str = "./eval_results") -> None:
        self._base_dir = Path(base_dir)
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_mutex = asyncio.Lock()

    async def _get_lock(self, batch_id: str) -> asyncio.Lock:
        async with self._lock_mutex:
            if batch_id not in self._locks:
                self._locks[batch_id] = asyncio.Lock()
            return self._locks[batch_id]

    def _batch_dir(self, batch_id: str) -> Path:
        p = self._base_dir / batch_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _question_dir(self, batch_id: str, question_id: str) -> Path:
        p = self._batch_dir(batch_id) / "questions" / question_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _llm_calls_dir(self, batch_id: str, question_id: str) -> Path:
        p = self._question_dir(batch_id, question_id) / "llm_calls"
        p.mkdir(parents=True, exist_ok=True)
        return p

    async def _write_json(self, path: Path, data: dict) -> None:
        tmp = path.with_suffix(".tmp")
        loop = asyncio.get_running_loop()
        content = json.dumps(data, ensure_ascii=False, default=str)
        await loop.run_in_executor(None, tmp.write_text, content)
        await loop.run_in_executor(None, os.replace, str(tmp), str(path))

    # ── Serialization helpers ──────────────────────────────────────────

    @staticmethod
    def _question_to_dict(q: EvalQuestion) -> dict:
        return {
            "question_id": q.question_id,
            "input_text": q.input_text,
            "scenario_id": q.scenario_id,
            "agent_name": q.agent_name,
        }

    @staticmethod
    def _request_to_dict(r: BatchEvalRequest) -> dict:
        return {
            "llm_provider": r.llm_provider,
            "llm_model": r.llm_model,
            "max_concurrency": r.max_concurrency,
            "questions": [
                LocalFileEvalStorage._question_to_dict(q) for q in r.questions
            ],
        }

    @staticmethod
    def _summary_to_dict(s: BatchSummary) -> dict:
        return {
            "batch_id": s.batch_id,
            "status": s.status,
            "total_questions": s.total_questions,
            "completed": s.completed,
            "failed": s.failed,
            "interrupted": s.interrupted,
            "created_at": s.created_at,
            "finished_at": s.finished_at,
            "error": s.error,
        }

    @staticmethod
    def _summary_from_dict(d: dict) -> BatchSummary:
        return BatchSummary(
            batch_id=d["batch_id"],
            status=d["status"],
            total_questions=d.get("total_questions", 0),
            completed=d.get("completed", 0),
            failed=d.get("failed", 0),
            interrupted=d.get("interrupted", 0),
            created_at=d.get("created_at", 0.0),
            finished_at=d.get("finished_at"),
            error=d.get("error"),
        )

    @staticmethod
    def _record_to_dict(r: LLMCallRecord) -> dict:
        return {
            "call_id": r.call_id,
            "question_id": r.question_id,
            "scenario_id": r.scenario_id,
            "agent_name": r.agent_name,
            "provider": r.provider,
            "model": r.model,
            "messages": r.messages,
            "response_content": r.response_content,
            "tool_calls": r.tool_calls,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
            "duration_ms": r.duration_ms,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "total_tokens": r.total_tokens,
            "error": r.error,
        }

    @staticmethod
    def _result_to_dict(r: QuestionResult) -> dict:
        return {
            "question_id": r.question_id,
            "scenario_id": r.scenario_id,
            "agent_name": r.agent_name,
            "input_text": r.input_text,
            "status": r.status,
            "error": r.error,
            "response": r.response,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
            "duration_ms": r.duration_ms,
            "llm_call_count": r.llm_call_count,
            "tool_call_count": r.tool_call_count,
        }

    # ── Protocol implementation ────────────────────────────────────────

    async def on_batch_started(self, batch_id: str, request: BatchEvalRequest) -> None:
        batch_dir = self._batch_dir(batch_id)
        await self._write_json(
            batch_dir / "request.json", self._request_to_dict(request)
        )
        initial = BatchSummary(
            batch_id=batch_id,
            status="running",
            total_questions=len(request.questions),
            created_at=time.time(),
        )
        await self._write_json(batch_dir / "batch.json", self._summary_to_dict(initial))

    async def on_question_started(self, batch_id: str, question: EvalQuestion) -> None:
        pass

    async def on_llm_call_recorded(self, batch_id: str, record: LLMCallRecord) -> None:
        calls_dir = self._llm_calls_dir(batch_id, record.question_id)
        await self._write_json(
            calls_dir / f"{record.call_id}.json", self._record_to_dict(record)
        )

    async def on_question_completed(
        self, batch_id: str, result: QuestionResult
    ) -> None:
        q_dir = self._question_dir(batch_id, result.question_id)
        await self._write_json(q_dir / "question.json", self._result_to_dict(result))
        lock = await self._get_lock(batch_id)
        async with lock:
            await self._update_batch_counters(batch_id)

    async def on_batch_completed(self, batch_id: str, summary: BatchSummary) -> None:
        lock = await self._get_lock(batch_id)
        async with lock:
            batch_dir = self._batch_dir(batch_id)
            await self._write_json(
                batch_dir / "batch.json", self._summary_to_dict(summary)
            )

    async def _update_batch_counters(self, batch_id: str) -> None:
        path = self._batch_dir(batch_id) / "batch.json"
        try:
            data = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return
        questions_dir = self._batch_dir(batch_id) / "questions"
        if questions_dir.exists():
            completed = failed = interrupted = 0
            for entry in questions_dir.iterdir():
                if not entry.is_dir():
                    continue
                qpath = entry / "question.json"
                if qpath.exists():
                    try:
                        qdata = json.loads(qpath.read_text())
                        st = qdata.get("status", "")
                        if st == "completed":
                            completed += 1
                        elif st == "failed":
                            failed += 1
                        elif st == "interrupted":
                            interrupted += 1
                    except (json.JSONDecodeError, OSError):
                        continue
            data["completed"] = completed
            data["failed"] = failed
            data["interrupted"] = interrupted
            await self._write_json(path, data)

    async def get_batch(self, batch_id: str) -> BatchSummary | None:
        path = self._batch_dir(batch_id) / "batch.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return self._summary_from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    async def list_batches(self) -> list[BatchSummary]:
        if not self._base_dir.exists():
            return []
        summaries: list[BatchSummary] = []
        for entry in sorted(self._base_dir.iterdir()):
            if not entry.is_dir():
                continue
            meta_path = entry / "batch.json"
            if not meta_path.exists():
                continue
            try:
                data = json.loads(meta_path.read_text())
                summaries.append(self._summary_from_dict(data))
            except (json.JSONDecodeError, KeyError):
                continue
        summaries.sort(key=lambda s: s.created_at, reverse=True)
        return summaries
