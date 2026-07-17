from __future__ import annotations

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
