"""系统路由：数据概览、健康检查、系统设置、操作日志。

说明：这里的「设置」只包含运行参数（并发数、延迟、超时、页数上限），
**不包含也不允许出现任何额度 / 计费 / 套餐概念** —— 这是一个纯个人求职辅助工具。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db
from ..enums import AuthStatus, LogCategory, TaskStatus
from ..models import AppSetting, JobRecord
from ..schemas import (
    ChartData,
    ChartSeries,
    DashboardSummary,
    JobFacet,
    LogRead,
    OverviewCards,
    QueueStatus,
    ScraperHealth,
    SystemSettingsRead,
    SystemSettingsUpdate,
    TaskRead,
    TopSkill,
)
from ..services import repository as repo
from ..services.auth_service import login_manager
from ..services.crypto_store import load_runtime_settings, save_runtime_settings
from ..services.logging_service import log_event
from ..services.queue_service import scan_stale_tasks, task_queue
from ..services.scraper_runner import (
    cdp_reachable,
    check_scraper_available,
    read_scraper_version,
)
from ..utils import utcnow
from .helpers import ok
from .helpers import page as build_page

router = APIRouter(prefix="/system", tags=["系统"])


# ============================================================
# 数据概览
# ============================================================
@router.get("/dashboard")
async def dashboard(
    db: AsyncSession = Depends(get_db),
    trend_days: int = Query(14, ge=3, le=60),
    recent_limit: int = Query(8, ge=1, le=50),
):
    """工作台首页所需的全部数据：4 张卡片 + 图表 + 最近任务。"""
    cards_raw = await repo.overview_cards(db)
    cards = OverviewCards(**cards_raw)

    # 运行状态判定（给顶部状态栏用）
    available, message = check_scraper_available()
    cdp_ok = cdp_reachable(settings.cdp_port)
    if not available:
        status_text, status_level = message, "error"
    elif login_manager.is_busy():
        status_text, status_level = "等待扫码授权", "warning"
    elif cards_raw["auth_status"] == "active":
        status_text, status_level = "服务正常 · 登录态有效", "ok"
    elif cdp_ok:
        status_text, status_level = "服务正常 · 待授权", "warning"
    else:
        status_text, status_level = "服务正常 · 浏览器未启动", "warning"

    # ---- 图表 ----
    salary_rows = await repo.salary_distribution(db)
    salary_chart = ChartData(
        categories=[bucket for bucket, _ in salary_rows],
        series=[ChartSeries(name="岗位数", data=[count for _, count in salary_rows])],
    )

    experience_rows = await repo.group_count(db, JobRecord.experience, limit=10)
    experience_chart = ChartData(
        categories=[value for value, _ in experience_rows],
        series=[ChartSeries(name="岗位数", data=[count for _, count in experience_rows])],
    )

    city_rows = await repo.group_count(db, JobRecord.city, limit=10)
    city_chart = ChartData(
        categories=[value for value, _ in city_rows],
        series=[ChartSeries(name="岗位数", data=[count for _, count in city_rows])],
    )

    trend_rows = await repo.daily_trend(db, days=trend_days)
    trend_chart = ChartData(
        categories=[day for day, _ in trend_rows],
        series=[ChartSeries(name="新增岗位", data=[count for _, count in trend_rows])],
    )

    skills = await repo.top_skills(db, limit=15)
    companies = await repo.group_count(db, JobRecord.company, limit=8)
    recent = await repo.recent_tasks(db, limit=recent_limit)

    summary = DashboardSummary(
        cards=cards,
        status_text=status_text,
        status_level=status_level,
        salary_distribution=salary_chart,
        experience_distribution=experience_chart,
        city_distribution=city_chart,
        daily_trend=trend_chart,
        top_skills=[TopSkill(skill=skill, count=count) for skill, count in skills],
        top_companies=[JobFacet(value=value, count=count) for value, count in companies],
        recent_tasks=[TaskRead.from_orm_task(task) for task in recent],
        disclaimer=settings.disclaimer,
    )
    return ok(summary.model_dump())


# ============================================================
# 健康检查
# ============================================================
@router.get("/health")
async def health():
    """轻量健康检查（给部署探针用）。"""
    return ok({"status": "ok", "version": settings.app_version, "time": utcnow().isoformat()})


def _scraper_health_message(
    available: bool, base_message: str, cdp_ok: bool, auth_active: bool
) -> str:
    """拼一句对用户真正有用的健康提示。

    注意：CDP 端口通只说明浏览器开着，**不代表已登录** —— 这两件事必须
    分开表述，否则用户会以为可以抓取了。
    """
    if not available:
        return base_message
    if not cdp_ok:
        return "爬虫脚本就绪，但专用浏览器未启动（CDP 端口未监听），请先到「账号授权」发起扫码登录"
    if not auth_active:
        return "浏览器已连接（CDP 端口正常），但尚未登录 BOSS 直聘，请到「账号授权」完成扫码登录"
    return "爬虫集成正常，登录态可用"


@router.get("/scraper-health")
async def scraper_health(db: AsyncSession = Depends(get_db)):
    """爬虫集成健康检查：脚本、依赖、CDP、profile、登录态。"""
    available, message = check_scraper_available()
    cdp_ok = cdp_reachable(settings.cdp_port)
    from ..services.auth_service import get_active_profile

    profile = await get_active_profile(db)
    profile_dir = Path(profile.profile_dir) if profile else None
    auth_active = bool(profile and profile.status == AuthStatus.ACTIVE.value)

    payload = ScraperHealth(
        scraper_dir=str(settings.scraper_path),
        entry_exists=settings.scraper_entry.is_file(),
        python_executable=settings.python_executable,
        version=read_scraper_version(),
        cdp_port=settings.cdp_port,
        cdp_reachable=cdp_ok,
        profile_dir=str(profile_dir) if profile_dir else None,
        profile_exists=bool(profile_dir and profile_dir.is_dir()),
        authorized=auth_active,
        message=_scraper_health_message(available, message, cdp_ok, auth_active),
    )
    return ok(payload.model_dump())


# ============================================================
# 系统设置
# ============================================================
def _effective_settings() -> dict:
    """合并 .env 默认值与运行时覆盖值。"""
    runtime = load_runtime_settings()
    data = {
        "max_concurrent_tasks": settings.max_concurrent_tasks,
        "task_delay_min": settings.task_delay_min,
        "task_delay_max": settings.task_delay_max,
        "max_retries": settings.max_retries,
        "max_pages_per_task": settings.max_pages_per_task,
        "task_timeout_seconds": settings.task_timeout_seconds,
        "auth_timeout_seconds": settings.auth_timeout_seconds,
        "request_budget": settings.request_budget,
    }
    for key, value in runtime.items():
        if key in data and value is not None:
            data[key] = value
    return data


def apply_runtime_overrides() -> None:
    """把运行时设置写回全局配置对象（启动时调用一次）。"""
    runtime = load_runtime_settings()
    for key, value in runtime.items():
        if hasattr(settings, key) and value is not None:
            try:
                setattr(settings, key, value)
            except (ValueError, TypeError):
                continue
    # 夹取到合法范围
    settings.max_concurrent_tasks = max(1, min(int(settings.max_concurrent_tasks), 4))
    settings.max_pages_per_task = max(1, min(int(settings.max_pages_per_task), 10))


@router.get("/settings")
async def get_settings(db: AsyncSession = Depends(get_db)):
    effective = _effective_settings()
    payload = SystemSettingsRead(
        app_name=settings.app_name,
        app_version=settings.app_version,
        database_dialect=settings.database_url.split(":", 1)[0],
        storage_root=str(settings.storage_path),
        cdp_port=settings.cdp_port,
        disclaimer=settings.disclaimer,
        **effective,
    )
    return ok(payload.model_dump())


@router.put("/settings")
async def update_settings(payload: SystemSettingsUpdate, db: AsyncSession = Depends(get_db)):
    """更新运行参数（持久化到 storage/runtime-settings.json）。"""
    updates = {key: value for key, value in payload.model_dump().items() if value is not None}
    if not updates:
        return ok(_effective_settings())

    # 夹取到安全范围，避免用户把并发调到 20 触发风控
    if "max_concurrent_tasks" in updates:
        updates["max_concurrent_tasks"] = max(1, min(int(updates["max_concurrent_tasks"]), 4))
    if "max_pages_per_task" in updates:
        updates["max_pages_per_task"] = max(1, min(int(updates["max_pages_per_task"]), 10))
    if "task_delay_min" in updates:
        updates["task_delay_min"] = max(0.0, float(updates["task_delay_min"]))
    if "task_delay_max" in updates:
        updates["task_delay_max"] = max(
            float(updates.get("task_delay_min", 0.0)), float(updates["task_delay_max"])
        )
    if "max_retries" in updates:
        updates["max_retries"] = max(0, min(int(updates["max_retries"]), 5))

    save_runtime_settings(updates)
    for key, value in updates.items():
        existing = await db.get(AppSetting, f"runtime.{key}")
        if existing is None:
            db.add(AppSetting(key=f"runtime.{key}", value=str(value)))
        else:
            existing.value = str(value)
    await db.commit()

    apply_runtime_overrides()
    await log_event(
        db,
        "更新系统运行参数",
        category=LogCategory.SYSTEM,
        detail=", ".join(f"{key}={value}" for key, value in updates.items()),
    )
    return ok(_effective_settings())


# ============================================================
# 日志
# ============================================================
@router.get("/logs")
async def list_logs(
    db: AsyncSession = Depends(get_db),
    page_number: int = Query(1, alias="page", ge=1),
    page_size: int = Query(50, ge=1, le=500),
    level: str | None = None,
    category: str | None = None,
    task_id: int | None = None,
):
    rows, total = await repo.list_logs(
        db,
        page=page_number,
        page_size=page_size,
        level=level,
        category=category,
        task_id=task_id,
    )
    items = [LogRead.model_validate(row).model_dump() for row in rows]
    return ok(build_page(items, total, page_number, page_size).model_dump())


@router.delete("/logs")
async def purge_logs(days: int = Query(30, ge=1, le=365), db: AsyncSession = Depends(get_db)):
    deleted = await repo.purge_logs(db, keep_days=days)
    return ok({"deleted": deleted})


# ============================================================
# 维护
# ============================================================
@router.post("/maintenance/scan-stale")
async def trigger_scan_stale():
    """手动触发一次「卡死任务」扫描。"""
    fixed = await scan_stale_tasks()
    return ok({"fixed": fixed})


@router.get("/queue")
async def queue_status():
    snapshot = await task_queue.snapshot_with_pending()
    return ok(QueueStatus(**snapshot.to_payload()).model_dump())


@router.get("/storage")
async def storage_usage():
    """统计 storage 目录占用（只读，供运维参考）。"""
    root = settings.storage_path
    total = 0
    breakdown: dict[str, int] = {}
    if root.is_dir():
        for child in root.iterdir():
            if not child.is_dir():
                continue
            size = sum(item.stat().st_size for item in child.rglob("*") if item.is_file())
            breakdown[child.name] = size
            total += size
    return ok(
        {
            "root": str(root),
            "total_bytes": total,
            "breakdown": breakdown,
            "disk_free_bytes": shutil.disk_usage(root if root.exists() else Path.cwd()).free,
        }
    )
