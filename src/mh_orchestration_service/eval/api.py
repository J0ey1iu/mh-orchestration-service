from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from mh_orchestration_service.api.dependencies import (
    get_current_permissions,
    get_current_user,
)
from mh_orchestration_service.eval.runner import run_scenario_eval
from mh_orchestration_service.eval.types import EvalInput, EvalJob, EvalJobConfig

logger = logging.getLogger("orchestration.eval")

router = APIRouter(prefix="/api/v1/eval", tags=["eval"])

_jobs: dict[str, EvalJob] = {}


class EvalInputSchema(BaseModel):
    input_text: str
    agent_name: str


class EvalJobConfigSchema(BaseModel):
    scenario_id: str
    description: str = ""
    inputs: list[EvalInputSchema]
    max_concurrency: int = 4
    cost_per_million_input_tokens: float | None = None
    cost_per_million_output_tokens: float | None = None


def _job_to_dict(job: EvalJob) -> dict:
    d: dict = {
        "job_id": job.job_id,
        "scenario_id": job.scenario_id,
        "status": job.status,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
        "error": job.error,
    }
    if job.output_dir:
        d["report_url"] = f"/api/v1/eval/results/{job.job_id}/report.html"
    if job.summary is not None:
        d["summary"] = {
            "task_name": job.summary.task_name,
            "description": job.summary.description,
            "agent_metadata_id": job.summary.agent_metadata_id,
            "total_runs": job.summary.total_runs,
            "completed": job.summary.completed,
            "failed": job.summary.failed,
            "interrupted": job.summary.interrupted,
            "total_time": job.summary.total_time,
            "avg_time": job.summary.avg_time,
            "total_tokens": job.summary.total_tokens,
            "total_cost": job.summary.total_cost,
            "output_path": job.summary.output_path,
            "runs": [
                {
                    "run_id": r.run_id,
                    "agent_metadata_id": r.agent_metadata_id,
                    "input_text": r.input_text,
                    "status": r.status,
                    "time_taken": r.time_taken,
                    "error": r.error,
                    "response": r.response,
                    "token_usage": {
                        "input_tokens": r.token_usage.input_tokens,
                        "output_tokens": r.token_usage.output_tokens,
                        "total_tokens": r.token_usage.total_tokens,
                        "total_cost": r.token_usage.total_cost,
                    }
                    if r.token_usage
                    else None,
                    "llm_call_count": r.llm_call_count,
                    "tool_call_count": r.tool_call_count,
                    "exceeded": r.exceeded,
                }
                for r in job.summary.runs
            ],
        }
    return d


@router.post("/jobs")
async def create_eval_job(
    request: Request,
    body: EvalJobConfigSchema,
    user_id: str = Depends(get_current_user),
    user_perms: list[str] = Depends(get_current_permissions),
):
    from minimal_harness.auth import match_permission

    if not match_permission(user_perms, "use:eval:*"):
        raise HTTPException(status_code=403, detail="Eval permission required")

    config = EvalJobConfig(
        scenario_id=body.scenario_id,
        description=body.description,
        inputs=[
            EvalInput(input_text=inp.input_text, agent_name=inp.agent_name)
            for inp in body.inputs
        ],
        max_concurrency=body.max_concurrency,
        cost_per_million_input_tokens=body.cost_per_million_input_tokens,
        cost_per_million_output_tokens=body.cost_per_million_output_tokens,
    )

    adapters = request.app.state.adapters
    output_dir = getattr(adapters.settings, "eval_results_dir", "./eval_results")
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    eval_dir = str(Path(output_dir) / f"{config.scenario_id}_{timestamp}")

    job = EvalJob(
        job_id=str(uuid.uuid4())[:8],
        scenario_id=config.scenario_id,
        status="running",
        created_at=time.time(),
        config=config,
        output_dir=eval_dir,
    )
    _jobs[job.job_id] = job

    asyncio.create_task(_run_job(request, user_id, job))

    return {"job_id": job.job_id, "status": "running"}


async def _run_job(request: Request, user_id: str, job: EvalJob) -> None:
    try:
        if job.config is None:
            raise RuntimeError("Job config is missing")
        summary = await run_scenario_eval(
            request=request,
            user_id=user_id,
            config=job.config,
            eval_dir=job.output_dir,
        )
        job.summary = summary
        job.status = "completed"
    except asyncio.CancelledError:
        job.status = "failed"
        job.error = "Job was cancelled"
    except Exception as exc:
        logger.exception("Eval job %s failed", job.job_id)
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
    finally:
        job.finished_at = time.time()


@router.get("/jobs")
async def list_eval_jobs(
    request: Request,
    user_id: str = Depends(get_current_user),
    user_perms: list[str] = Depends(get_current_permissions),
):
    from minimal_harness.auth import match_permission

    if not match_permission(user_perms, "use:eval:*"):
        raise HTTPException(status_code=403, detail="Eval permission required")

    return [
        {
            "job_id": j.job_id,
            "scenario_id": j.scenario_id,
            "status": j.status,
            "created_at": j.created_at,
            "finished_at": j.finished_at,
            "error": j.error,
        }
        for j in sorted(_jobs.values(), key=lambda j: j.created_at or 0.0, reverse=True)
    ]


@router.get("/jobs/{job_id}")
async def get_eval_job(
    job_id: str,
    request: Request,
    user_id: str = Depends(get_current_user),
    user_perms: list[str] = Depends(get_current_permissions),
):
    from minimal_harness.auth import match_permission

    if not match_permission(user_perms, "use:eval:*"):
        raise HTTPException(status_code=403, detail="Eval permission required")

    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_to_dict(job)


@router.get("/results/{job_id}/{filename:path}")
async def serve_eval_result(
    job_id: str,
    filename: str,
    request: Request,
    user_id: str = Depends(get_current_user),
    user_perms: list[str] = Depends(get_current_permissions),
):
    from minimal_harness.auth import match_permission

    if not match_permission(user_perms, "use:eval:*"):
        raise HTTPException(status_code=403, detail="Eval permission required")

    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    base = Path(job.output_dir).resolve()
    filepath = (base / filename).resolve()
    try:
        filepath.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=404, detail="File not found")
    if not filepath.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(filepath))
