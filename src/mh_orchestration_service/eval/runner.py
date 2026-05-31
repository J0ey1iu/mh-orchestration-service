from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi import Request
from minimal_harness.eval import (
    EvalCollector,
    EvalPersistence,
    EvalRunRecord,
    EvalSummary,
    generate_html_report,
)
from minimal_harness.types import AgentEnd

from mh_orchestration_service.eval.types import EvalInput, EvalJobConfig
from mh_orchestration_service.services.database import get_session_store
from mh_orchestration_service.services.runtime_service import create_runtime


async def run_scenario_eval(
    request: Request,
    user_id: str,
    config: EvalJobConfig,
    eval_dir: str,
) -> EvalSummary:
    adapters = request.app.state.adapters
    scenarios = await adapters.management_provider.list_scenarios()
    scenario = next((s for s in scenarios if s["id"] == config.scenario_id), None)
    if scenario is None:
        raise ValueError(f"Scenario '{config.scenario_id}' not found")

    agent_tools: dict[str, list[str]] = {}
    for a in scenario.get("agents", []):
        agent_tools[a["name"]] = a.get("tool_names", [])

    for inp in config.inputs:
        if inp.agent_name not in agent_tools:
            raise ValueError(
                f"Agent '{inp.agent_name}' not found in scenario '{config.scenario_id}'"
            )

    persistence = EvalPersistence(eval_dir)

    semaphore = asyncio.Semaphore(config.max_concurrency)
    runs: list[EvalRunRecord] = []
    _cancelled = False

    async def run_single(eval_input: EvalInput) -> EvalRunRecord:
        nonlocal _cancelled
        agent_name = eval_input.agent_name
        tool_names = agent_tools.get(agent_name, [])

        run_record = EvalRunRecord(
            agent_metadata_id=agent_name,
            input_text=eval_input.input_text,
            status="running",
            started_at=time.time(),
        )
        collector = EvalCollector(run_record.run_id, persistence)

        try:
            async with semaphore:
                if _cancelled:
                    run_record.status = "interrupted"
                else:
                    session_id = f"eval_{run_record.run_id}"
                    store = await get_session_store()
                    await store.create_session(
                        session_id=session_id,
                        agent_name=agent_name,
                        user_id=user_id,
                        scenario_id=config.scenario_id,
                    )

                    runtime, _, _, _ = await create_runtime(
                        request=request,
                        user_id=user_id,
                        agent_name=agent_name,
                        tool_names=tool_names,
                        session_store=store,
                        session_id=session_id,
                    )

                    events = await runtime.run_batch(
                        user_input=[{"type": "text", "text": eval_input.input_text}],
                        agent_metadata_id=agent_name,
                        memory_id=session_id,
                        tool_names=tool_names,
                    )

                    for event in events:
                        collector.consume_event(event)

                    for event in events:
                        if isinstance(event, AgentEnd):
                            run_record.response = event.response
                            run_record.time_taken = event.time_taken
                            run_record.exceeded = event.exceeded
                            if event.interrupted:
                                run_record.status = "interrupted"
                            elif event.error:
                                run_record.status = "failed"
                                run_record.error = event.error
                            else:
                                run_record.status = "completed"
                            break

        except asyncio.CancelledError:
            run_record.status = "interrupted"
        except Exception as exc:
            run_record.status = "failed"
            run_record.error = f"{type(exc).__name__}: {exc}"

        run_record.ended_at = time.time()
        if run_record.time_taken is None:
            end = run_record.ended_at
            start = run_record.started_at
            if end is not None and start is not None:
                run_record.time_taken = end - start
        run_record.llm_call_count = collector.llm_call_count
        run_record.tool_call_count = collector.tool_call_count

        tu = collector.token_usage
        if config.cost_per_million_input_tokens is not None:
            tu.input_cost = (
                tu.input_tokens / 1_000_000 * config.cost_per_million_input_tokens
            )
        if config.cost_per_million_output_tokens is not None:
            tu.output_cost = (
                tu.output_tokens / 1_000_000 * config.cost_per_million_output_tokens
            )
        total = 0.0
        if tu.input_cost is not None:
            total += tu.input_cost
        if tu.output_cost is not None:
            total += tu.output_cost
        if tu.input_cost is not None or tu.output_cost is not None:
            tu.total_cost = total
        run_record.token_usage = tu

        persistence.write_run_summary(run_record)
        persistence.close_run(run_record.run_id)

        return run_record

    tasks = [asyncio.create_task(run_single(inp)) for inp in config.inputs]

    try:
        for coro in asyncio.as_completed(tasks):
            run_record = await coro
            runs.append(run_record)
    except asyncio.CancelledError:
        _cancelled = True
        for t in tasks:
            if not t.done():
                t.cancel()
        for t in tasks:
            try:
                runs.append(await t)
            except (asyncio.CancelledError, Exception):
                pass

    runs.sort(key=lambda r: r.started_at or 0.0)
    summary = _build_summary(config, runs, eval_dir)
    persistence.write_summary(summary)

    report_path = Path(eval_dir) / "report.html"
    generate_html_report(summary, str(report_path))

    persistence.close_all()
    return summary


def _build_summary(
    config: EvalJobConfig,
    runs: list[EvalRunRecord],
    output_path: str,
) -> EvalSummary:
    completed = sum(1 for r in runs if r.status == "completed")
    failed = sum(1 for r in runs if r.status == "failed")
    interrupted = sum(1 for r in runs if r.status == "interrupted")
    total_time = sum((r.time_taken or 0.0) for r in runs)
    avg_time = total_time / len(runs) if runs else 0.0
    total_tokens = sum(
        (r.token_usage.total_tokens if r.token_usage else 0) for r in runs
    )
    costs = [
        r.token_usage.total_cost
        for r in runs
        if r.token_usage and r.token_usage.total_cost is not None
    ]
    total_cost = sum(costs) if costs else None

    return EvalSummary(
        task_name=config.scenario_id,
        description=config.description,
        agent_metadata_id=config.scenario_id,
        total_runs=len(runs),
        completed=completed,
        failed=failed,
        interrupted=interrupted,
        total_time=total_time,
        avg_time=avg_time,
        total_tokens=total_tokens,
        total_cost=total_cost,
        runs=runs,
        output_path=output_path,
    )
