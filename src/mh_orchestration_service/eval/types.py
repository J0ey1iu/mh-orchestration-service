from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from minimal_harness.eval import EvalSummary


@dataclass
class EvalInput:
    input_text: str
    agent_name: str


@dataclass
class EvalJobConfig:
    scenario_id: str
    description: str = ""
    inputs: list[EvalInput] = field(default_factory=list)
    max_concurrency: int = 4
    cost_per_million_input_tokens: float | None = None
    cost_per_million_output_tokens: float | None = None


@dataclass
class EvalJob:
    job_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    scenario_id: str = ""
    status: str = "pending"
    created_at: float | None = None
    finished_at: float | None = None
    config: EvalJobConfig | None = None
    summary: EvalSummary | None = None
    output_dir: str = ""
    error: str | None = None
