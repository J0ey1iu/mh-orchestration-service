from __future__ import annotations

from pydantic import BaseModel, Field


class ConfigSchema(BaseModel):
    """Deployment configuration.

    Every field **without a default** must be supplied by the deployer
    (via env vars or a config centre).  Fields with defaults are optional.
    """

    # ── Database ─────────────────────────────────────
    db_path: str = "./sessions.db"
    db_auto_schema: bool = False

    # ── CORS ────────────────────────────────────────
    cors_origins: list[str] = Field(default_factory=list)

    # ── Built-in Agents (demo) ──────────────────────
    enable_builtin_agents: bool = False

    dev_mode: bool = False

    # ── Evaluation ──────────────────────────────────
    enable_eval: bool = True
    eval_results_dir: str = "./eval_results"

    # ── Logging ─────────────────────────────────────
    log_level: str = "INFO"

    @property
    def database_url(self) -> str:
        return self.db_path
