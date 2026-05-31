from __future__ import annotations

from pydantic import BaseModel, Field


class ConfigSchema(BaseModel):
    """框架需要的配置声明。

    部署方必须为所有**无默认值**的字段赋值（通过环境变量或配置中心）。
    有默认值的字段可选，缺失时使用默认值。
    """

    # ── 数据库 ─────────────────────────────────────
    db_type: str  # "sqlite" | "opengauss"
    db_path: str  # sqlite 文件路径

    db_host: str = ""  # opengauss 主机
    db_port: int = 5432  # 数据库端口
    db_name: str = ""  # 数据库名
    db_user: str = ""  # 数据库用户
    db_password: str = ""  # 数据库密码（敏感）
    db_auto_schema: bool = False  # 自动建表

    # ── CORS ────────────────────────────────────────
    cors_origins: list[str] = Field(default_factory=list)

    # ── 内置 Agent（样例功能） ──────────────────────
    enable_builtin_agents: bool = False  # 开箱即用演示，生产环境请关闭

    dev_mode: bool = False  # 开发模式：暴露 dev 路由和前端静态文件

    # ── 评测 ────────────────────────────────────────
    enable_eval: bool = True  # 是否暴露评测接口
    eval_results_dir: str = "./eval_results"  # 评估结果输出目录

    # ── 日志 ────────────────────────────────────────
    log_level: str = "INFO"  # 日志级别，参考 MH_LOG_LEVEL

    @property
    def database_url(self) -> str:
        if self.db_type == "sqlite":
            return self.db_path
        return f"postgresql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"
