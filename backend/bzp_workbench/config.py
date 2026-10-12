"""应用配置。

所有配置项都可以通过环境变量或 ``.env`` 覆盖，统一前缀 ``BZP_``。
相对路径一律以 ``backend/`` 目录为基准解析，避免因为启动目录不同而漂移。
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/bzp_workbench/config.py -> backend/
BACKEND_DIR = Path(__file__).resolve().parent.parent


def _resolve(path_value: str) -> Path:
    """把配置里的路径解析成绝对路径（相对的按 backend/ 解析）。"""
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = (BACKEND_DIR / path).resolve()
    return path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BZP_",
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- 基础 ----------
    app_name: str = "智能求职辅助工作台"
    app_version: str = "1.0.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    api_prefix: str = "/api"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---------- 数据库 ----------
    database_url: str = "sqlite+aiosqlite:///./storage/boss_workbench.db"
    db_echo: bool = False

    # ---------- 目录 ----------
    storage_root: str = "./storage"
    scraper_dir: str = "../boss-zhipin-scraper"
    scraper_python: str = ""

    # ---------- 爬虫运行参数 ----------
    cdp_port: int = 9222
    task_timeout_seconds: int = 1800
    auth_timeout_seconds: int = 300
    max_pages_per_task: int = 10
    # 抓取前若 CDP 端口没监听，是否自动拉起专用浏览器
    autostart_browser: bool = True
    # 自动拉起浏览器的最长等待秒数（超时就交给抓取自己报错）
    browser_start_timeout_seconds: int = 20

    # ---------- 队列 ----------
    max_concurrent_tasks: int = 2
    task_delay_min: float = 3.0
    task_delay_max: float = 5.0
    max_retries: int = 1

    # ---------- 合规 ----------
    request_budget: int = 500
    disclaimer: str = (
        "本工具仅供个人学习与求职分析使用，数据来源于公开职位页面，"
        "严禁用于商业倒卖或大规模抓取。请务必控制抓取频率，遵守目标网站用户协议。"
    )

    # ---------- AI 对话 ----------
    #: 用假模型 echo 驱动对话（不访问真实 API）。
    #: 本地开发默认开启，方便在不消耗额度的前提下调试前端交互；生产必须关闭。
    fake_llm: bool = True
    #: 单次回复的最大输出 token
    chat_max_tokens: int = 2048
    #: 单个附件的大小上限（与前端校验保持一致）
    chat_max_image_bytes: int = 8 * 1024 * 1024
    chat_max_doc_bytes: int = 20 * 1024 * 1024
    chat_max_attachments: int = 5

    # ---------- 安全 ----------
    secret_key: str = Field(default_factory=lambda: secrets.token_urlsafe(32))

    # ---------- 维护 ----------
    stale_task_scan_seconds: int = 120

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------
    @field_validator("max_concurrent_tasks")
    @classmethod
    def _clamp_concurrency(cls, value: int) -> int:
        """并发上限做一个硬性夹取，防止有人配成 50 直接触发风控。"""
        return max(1, min(int(value), 4))

    @field_validator("max_pages_per_task")
    @classmethod
    def _clamp_pages(cls, value: int) -> int:
        return max(1, min(int(value), 10))

    @field_validator("task_delay_max")
    @classmethod
    def _delay_order(cls, value: float, info) -> float:
        low = info.data.get("task_delay_min", 3.0)
        return max(float(value), float(low))

    # ------------------------------------------------------------------
    # 派生路径
    # ------------------------------------------------------------------
    @property
    def storage_path(self) -> Path:
        return _resolve(self.storage_root)

    @property
    def scraper_path(self) -> Path:
        return _resolve(self.scraper_dir)

    @property
    def scraper_entry(self) -> Path:
        """爬虫主脚本路径。"""
        return self.scraper_path / "scripts" / "boss_cdp_raw.py"

    @property
    def python_executable(self) -> str:
        return self.scraper_python or os.environ.get("BZP_SCRAPER_PYTHON") or _current_python()

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def profile_dir(self, profile_hash: str) -> Path:
        """某个授权档案的 Chrome 隔离 profile 目录。"""
        return self.storage_path / "chrome-profiles" / profile_hash

    def result_dir(self, task_id: int | str) -> Path:
        """某个任务的抓取结果目录。"""
        return self.storage_path / "job-result" / f"task-{task_id}"

    def log_dir(self, task_id: int | str) -> Path:
        return self.storage_path / "logs" / f"task-{task_id}"

    def ensure_directories(self) -> None:
        """启动时把需要的目录一次性建好。"""
        for path in (
            self.storage_path,
            self.storage_path / "chrome-profiles",
            self.storage_path / "job-result",
            self.storage_path / "logs",
            self.storage_path / "auth",
        ):
            path.mkdir(parents=True, exist_ok=True)


def _current_python() -> str:
    """当前解释器路径，用于调用爬虫脚本。"""
    import sys

    return sys.executable


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# 便于 from .config import settings 直接使用
settings = get_settings()
