"""系统日志与运行日志。"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..enums import LogCategory, LogLevel
from ..models import SystemLog

logger = logging.getLogger("bzp")

_LOG_FILE = settings.storage_path / "logs" / "workbench.log"


def configure_logging() -> None:
    """配置标准库日志：控制台 + 落盘。"""
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("bzp")
    if root.handlers:
        return
    root.setLevel(logging.DEBUG if settings.debug else logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


async def log_event(
    session: AsyncSession | None,
    message: str,
    *,
    level: LogLevel = LogLevel.INFO,
    category: LogCategory = LogCategory.SYSTEM,
    detail: str | None = None,
    task_id: int | None = None,
    auth_profile_id: int | None = None,
    commit: bool = True,
) -> None:
    """写一条系统日志：同时进标准库日志和数据库。

    ``session`` 为 None 时只写标准库日志（例如数据库不可用的降级路径）。
    """
    text = f"[{category.value}] {message}"
    if level is LogLevel.ERROR:
        logger.error(text)
    elif level is LogLevel.WARNING:
        logger.warning(text)
    else:
        logger.info(text)

    if session is None:
        return

    session.add(
        SystemLog(
            level=level.value,
            category=category.value,
            message=message[:4000],
            detail=detail[:8000] if detail else None,
            task_id=task_id,
            auth_profile_id=auth_profile_id,
        )
    )
    if commit:
        await session.commit()


def task_log_path(task_id: int | str) -> Path:
    """某个任务的爬虫输出日志路径。"""
    directory = settings.log_dir(task_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "scraper.log"
