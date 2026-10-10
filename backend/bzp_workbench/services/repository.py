"""数据访问层：任务、岗位、统计。

把 SQL 集中在这里，路由层只调用语义化方法。
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..enums import AuthStatus, TaskStatus
from ..models import AuthProfile, JobRecord, ScrapeTask, SystemLog
from ..parsing import SALARY_BUCKET_ORDER, salary_bucket
from ..utils import CST, day_bounds_cst, to_naive_utc, utcnow

# ============================================================
# 任务
# ============================================================


async def create_task(db: AsyncSession, payload: dict[str, Any]) -> ScrapeTask:
    task = ScrapeTask(**payload)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def get_task(db: AsyncSession, task_id: int) -> ScrapeTask | None:
    return await db.get(ScrapeTask, task_id)


async def list_tasks(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    keyword: str | None = None,
    city: str | None = None,
) -> tuple[list[ScrapeTask], int]:
    statement: Select = select(ScrapeTask)
    count_statement = select(func.count()).select_from(ScrapeTask)

    conditions = []
    if status:
        conditions.append(ScrapeTask.status == status)
    if keyword:
        conditions.append(ScrapeTask.keyword.like(f"%{keyword}%"))
    if city:
        conditions.append(ScrapeTask.city == city)
    for condition in conditions:
        statement = statement.where(condition)
        count_statement = count_statement.where(condition)

    total = int((await db.execute(count_statement)).scalar() or 0)
    statement = (
        statement.order_by(ScrapeTask.created_at.desc(), ScrapeTask.id.desc())
        .offset(max(0, (page - 1) * page_size))
        .limit(page_size)
    )
    rows = (await db.execute(statement)).scalars().all()
    return list(rows), total


async def recent_tasks(db: AsyncSession, limit: int = 8) -> list[ScrapeTask]:
    statement = select(ScrapeTask).order_by(ScrapeTask.created_at.desc(), ScrapeTask.id.desc()).limit(limit)
    return list((await db.execute(statement)).scalars().all())


async def claim_pending_tasks(db: AsyncSession, limit: int) -> list[ScrapeTask]:
    """取出待执行任务（先进先出）。

    注意：调用方负责把状态改成 running，避免重复调度。
    """
    statement = (
        select(ScrapeTask)
        .where(ScrapeTask.status == TaskStatus.PENDING.value)
        .order_by(ScrapeTask.created_at.asc(), ScrapeTask.id.asc())
        .limit(limit)
    )
    return list((await db.execute(statement)).scalars().all())


async def update_task(db: AsyncSession, task: ScrapeTask, **fields: Any) -> ScrapeTask:
    for key, value in fields.items():
        setattr(task, key, value)
    task.updated_at = utcnow().replace(tzinfo=None)
    await db.commit()
    await db.refresh(task)
    return task


async def delete_task(db: AsyncSession, task: ScrapeTask) -> None:
    """删除任务。

    ``JobRecord.first_task_id`` / ``last_task_id`` 是 ``ON DELETE SET NULL``，
    但 SQLite 默认不开启外键级联，所以这里手动把归属清空，避免留下悬挂引用。
    岗位本身保留（它是全局去重的资产，不属于单个任务）。
    """
    await db.execute(
        JobRecord.__table__.update()
        .where(JobRecord.first_task_id == task.id)
        .values(first_task_id=None)
    )
    await db.execute(
        JobRecord.__table__.update()
        .where(JobRecord.last_task_id == task.id)
        .values(last_task_id=None)
    )
    await db.delete(task)
    await db.commit()


async def count_tasks_by_status(db: AsyncSession) -> dict[str, int]:
    statement = select(ScrapeTask.status, func.count()).group_by(ScrapeTask.status)
    rows = (await db.execute(statement)).all()
    return {str(status): int(count) for status, count in rows}


async def count_tasks_since(db: AsyncSession, start: datetime, end: datetime) -> int:
    statement = select(func.count()).select_from(ScrapeTask).where(
        ScrapeTask.created_at >= start, ScrapeTask.created_at < end
    )
    return int((await db.execute(statement)).scalar() or 0)


async def running_tasks(db: AsyncSession) -> list[ScrapeTask]:
    statement = select(ScrapeTask).where(ScrapeTask.status == TaskStatus.RUNNING.value)
    return list((await db.execute(statement)).scalars().all())


async def stale_running_tasks(db: AsyncSession, older_than_seconds: int) -> list[ScrapeTask]:
    """找出「标记为运行中但实际已卡死」的任务。"""
    cutoff = utcnow().replace(tzinfo=None) - timedelta(seconds=older_than_seconds)
    statement = select(ScrapeTask).where(
        ScrapeTask.status == TaskStatus.RUNNING.value,
        or_(ScrapeTask.started_at.is_(None), ScrapeTask.started_at < cutoff),
    )
    return list((await db.execute(statement)).scalars().all())


# ============================================================
# 岗位
# ============================================================

# 重复抓到时允许被覆盖的列（老数据缺详情，新数据补上）
_UPDATABLE_COLUMNS = (
    "title",
    "company",
    "salary",
    "salary_source",
    "salary_min",
    "salary_max",
    "salary_avg",
    "city",
    "location",
    "experience",
    "degree",
    "company_scale",
    "company_stage",
    "company_industry",
    "boss_name",
    "boss_title",
    "boss_active_status",
    "tags",
    "skills_raw",
    "welfare",
    "skill_tags",
    "skill_keywords",
    "jd",
    "job_duty",
    "job_requirement",
    "job_link",
    "company_link",
    "security_id",
    "lid",
    "is_new",
    "detail_fetched",
    "raw_payload",
    "last_task_id",
    "scraped_at",
    "updated_at",
)


async def sync_jobs(db: AsyncSession, task: ScrapeTask, jobs: list[dict[str, Any]]) -> dict[str, int]:
    """把归一化后的岗位写入数据库（按 ``job_id`` upsert）。

    返回统计：``{"inserted": n, "updated": n, "unchanged": n}``。
    """
    if not jobs:
        return {"inserted": 0, "updated": 0, "unchanged": 0}

    job_ids = [job["job_id"] for job in jobs]
    existing_rows = (
        await db.execute(select(JobRecord).where(JobRecord.job_id.in_(job_ids)))
    ).scalars().all()
    existing = {row.job_id: row for row in existing_rows}

    inserted = updated = unchanged = 0
    now = utcnow().replace(tzinfo=None)

    for payload in jobs:
        job_id = payload["job_id"]
        row = existing.get(job_id)
        if row is None:
            record = JobRecord(
                **payload,
                first_task_id=task.id,
                last_task_id=task.id,
                scraped_at=now,
            )
            db.add(record)
            inserted += 1
            continue

        changed = False
        for column in _UPDATABLE_COLUMNS:
            if column not in payload:
                continue
            new_value = payload[column]
            if column in ("last_task_id", "scraped_at", "updated_at"):
                continue
            old_value = getattr(row, column, None)
            # JD 只补不覆盖为空，避免后续无详情任务把已有全文洗掉
            if column in ("jd", "job_duty", "job_requirement") and not new_value:
                continue
            if new_value is None and old_value is not None:
                continue
            if old_value != new_value:
                setattr(row, column, new_value)
                changed = True

        row.last_task_id = task.id
        row.scraped_at = now
        row.updated_at = now
        if changed:
            updated += 1
        else:
            unchanged += 1

    await db.commit()
    return {"inserted": inserted, "updated": updated, "unchanged": unchanged}


def _apply_job_filters(
    statement: Select,
    *,
    keyword: str | None = None,
    city: str | None = None,
    task_id: int | None = None,
    experience: str | None = None,
    degree: str | None = None,
    company_scale: str | None = None,
    salary_min: int | None = None,
    salary_max: int | None = None,
    skill: str | None = None,
    only_new: bool | None = None,
    only_favorite: bool | None = None,
    only_with_jd: bool | None = None,
) -> Select:
    if keyword:
        like = f"%{keyword}%"
        statement = statement.where(
            or_(
                JobRecord.title.like(like),
                JobRecord.company.like(like),
                JobRecord.jd.like(like),
                JobRecord.skill_keywords.like(like),
            )
        )
    if city:
        statement = statement.where(JobRecord.city == city)
    if task_id is not None:
        statement = statement.where(
            or_(JobRecord.first_task_id == task_id, JobRecord.last_task_id == task_id)
        )
    if experience:
        statement = statement.where(JobRecord.experience == experience)
    if degree:
        statement = statement.where(JobRecord.degree == degree)
    if company_scale:
        statement = statement.where(JobRecord.company_scale == company_scale)
    if salary_min is not None:
        statement = statement.where(JobRecord.salary_avg >= salary_min)
    if salary_max is not None:
        statement = statement.where(JobRecord.salary_avg <= salary_max)
    if skill:
        statement = statement.where(JobRecord.skill_keywords.like(f"%{skill}%"))
    if only_new:
        statement = statement.where(JobRecord.is_new.is_(True))
    if only_favorite:
        statement = statement.where(JobRecord.is_favorite.is_(True))
    if only_with_jd:
        statement = statement.where(JobRecord.jd.is_not(None), JobRecord.jd != "")
    return statement


async def list_jobs(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
    order_by: str = "created_desc",
    **filters: Any,
) -> tuple[list[JobRecord], int]:
    statement: Select = select(JobRecord)
    count_statement = select(func.count()).select_from(JobRecord)
    statement = _apply_job_filters(statement, **filters)
    count_statement = _apply_job_filters(count_statement, **filters)

    total = int((await db.execute(count_statement)).scalar() or 0)

    order_map = {
        "created_desc": (JobRecord.created_at.desc(), JobRecord.id.desc()),
        "salary_desc": (JobRecord.salary_avg.is_(None), JobRecord.salary_avg.desc()),
        "salary_asc": (JobRecord.salary_avg.is_(None), JobRecord.salary_avg.asc()),
        "scraped_desc": (JobRecord.scraped_at.desc(), JobRecord.id.desc()),
    }
    statement = statement.order_by(*order_map.get(order_by, order_map["created_desc"]))
    statement = statement.offset(max(0, (page - 1) * page_size)).limit(page_size)

    rows = (await db.execute(statement)).scalars().all()
    return list(rows), total


async def iterate_jobs(db: AsyncSession, **filters: Any) -> Sequence[JobRecord]:
    """导出用：一次性取出满足条件的岗位。"""
    statement: Select = select(JobRecord)
    statement = _apply_job_filters(statement, **filters)
    statement = statement.order_by(JobRecord.created_at.desc())
    return list((await db.execute(statement)).scalars().all())


async def get_job(db: AsyncSession, job_id: int) -> JobRecord | None:
    return await db.get(JobRecord, job_id)


async def get_job_by_biz_id(db: AsyncSession, biz_id: str) -> JobRecord | None:
    statement = select(JobRecord).where(JobRecord.job_id == biz_id)
    return (await db.execute(statement)).scalar_one_or_none()


async def set_favorite(db: AsyncSession, job: JobRecord, favorite: bool) -> JobRecord:
    job.is_favorite = favorite
    job.updated_at = utcnow().replace(tzinfo=None)
    await db.commit()
    await db.refresh(job)
    return job


async def delete_jobs(db: AsyncSession, job_ids: list[int]) -> int:
    if not job_ids:
        return 0
    result = await db.execute(delete(JobRecord).where(JobRecord.id.in_(job_ids)))
    await db.commit()
    return int(result.rowcount or 0)


# ============================================================
# 统计 / 图表
# ============================================================
async def count_jobs(db: AsyncSession) -> int:
    return int((await db.execute(select(func.count()).select_from(JobRecord))).scalar() or 0)


async def count_jobs_since(db: AsyncSession, start: datetime, end: datetime) -> int:
    statement = select(func.count()).select_from(JobRecord).where(
        JobRecord.created_at >= start, JobRecord.created_at < end
    )
    return int((await db.execute(statement)).scalar() or 0)


async def count_favorite_jobs(db: AsyncSession) -> int:
    statement = select(func.count()).select_from(JobRecord).where(JobRecord.is_favorite.is_(True))
    return int((await db.execute(statement)).scalar() or 0)


async def salary_distribution(db: AsyncSession) -> list[tuple[str, int]]:
    """按薪资区间聚合。"""
    statement = select(JobRecord.salary_avg)
    rows = (await db.execute(statement)).scalars().all()
    counter: Counter[str] = Counter()
    for value in rows:
        counter[salary_bucket(value)] += 1
    ordered = [(bucket, counter.get(bucket, 0)) for bucket in SALARY_BUCKET_ORDER]
    return [item for item in ordered if item[1] > 0] or ordered


async def group_count(db: AsyncSession, column, limit: int = 10) -> list[tuple[str, int]]:
    statement = (
        select(column, func.count().label("count"))
        .where(column.is_not(None), column != "")
        .group_by(column)
        .order_by(func.count().desc())
        .limit(limit)
    )
    rows = (await db.execute(statement)).all()
    return [(str(value), int(count)) for value, count in rows]


async def daily_trend(db: AsyncSession, days: int = 14) -> list[tuple[str, int]]:
    """最近 N 天的岗位入库趋势（按东八区自然日）。"""
    start = utcnow().replace(tzinfo=None) - timedelta(days=days)
    statement = select(JobRecord.created_at).where(JobRecord.created_at >= start)
    rows = (await db.execute(statement)).scalars().all()

    counter: Counter[str] = Counter()
    for value in rows:
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        counter[aware.astimezone(CST).strftime("%m-%d")] += 1

    ordered: list[tuple[str, int]] = []
    now_cst = utcnow().astimezone(CST)
    for offset in range(days - 1, -1, -1):
        day = (now_cst - timedelta(days=offset)).strftime("%m-%d")
        ordered.append((day, counter.get(day, 0)))
    return ordered


async def top_skills(db: AsyncSession, limit: int = 15) -> list[tuple[str, int]]:
    """按技能标签出现次数排序（skill_keywords 是 JSON 数组字符串）。"""
    statement = select(JobRecord.skill_keywords).where(
        JobRecord.skill_keywords.is_not(None), JobRecord.skill_keywords != ""
    )
    rows = (await db.execute(statement)).scalars().all()
    counter: Counter[str] = Counter()
    for raw in rows:
        try:
            items = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(items, list):
            continue
        for item in {str(entry).strip() for entry in items if str(entry).strip()}:
            counter[item] += 1
    return counter.most_common(limit)


async def job_facets(db: AsyncSession) -> dict[str, Any]:
    """岗位筛选面板需要的可选值。"""
    cities = await group_count(db, JobRecord.city, limit=30)
    experiences = await group_count(db, JobRecord.experience, limit=15)
    degrees = await group_count(db, JobRecord.degree, limit=15)
    companies = await group_count(db, JobRecord.company, limit=10)
    skills = [{"value": skill, "count": count} for skill, count in await top_skills(db, limit=24)]

    bounds = (
        await db.execute(
            select(func.min(JobRecord.salary_avg), func.max(JobRecord.salary_avg)).where(
                JobRecord.salary_avg.is_not(None)
            )
        )
    ).one()

    return {
        "cities": [{"value": value, "count": count} for value, count in cities],
        "experiences": [{"value": value, "count": count} for value, count in experiences],
        "degrees": [{"value": value, "count": count} for value, count in degrees],
        "companies": [{"value": value, "count": count} for value, count in companies],
        "skills": skills,
        "salary_min": int(bounds[0]) if bounds and bounds[0] is not None else None,
        "salary_max": int(bounds[1]) if bounds and bounds[1] is not None else None,
    }


# ============================================================
# 概览卡片
# ============================================================
async def overview_cards(db: AsyncSession) -> dict[str, Any]:
    start, end = day_bounds_cst()

    total_jobs = await count_jobs(db)
    today_new_jobs = await count_jobs_since(db, start, end)
    favorite_jobs = await count_favorite_jobs(db)

    status_counts = await count_tasks_by_status(db)
    active_tasks = status_counts.get(TaskStatus.PENDING.value, 0) + status_counts.get(
        TaskStatus.RUNNING.value, 0
    )
    failed_tasks = status_counts.get(TaskStatus.FAILED.value, 0) + status_counts.get(
        TaskStatus.NEEDS_ATTENTION.value, 0
    )
    total_tasks = sum(status_counts.values())
    today_tasks = await count_tasks_since(db, start, end)

    profile_row = (
        await db.execute(select(AuthProfile).order_by(AuthProfile.is_default.desc()).limit(1))
    ).scalar_one_or_none()

    auth_status = profile_row.status if profile_row else AuthStatus.UNKNOWN.value
    auth_label = AuthStatus(auth_status).label if auth_status in {s.value for s in AuthStatus} else "未校验"

    return {
        "total_jobs": total_jobs,
        "today_new_jobs": today_new_jobs,
        "active_tasks": active_tasks,
        "auth_status": auth_status,
        "auth_status_label": auth_label,
        "auth_account": profile_row.account_label if profile_row else None,
        "auth_verified_at": profile_row.last_verified_at if profile_row else None,
        "total_tasks": total_tasks,
        "succeeded_tasks": status_counts.get(TaskStatus.SUCCEEDED.value, 0),
        "failed_tasks": failed_tasks,
        "today_tasks": today_tasks,
        "favorite_jobs": favorite_jobs,
    }


# ============================================================
# 日志
# ============================================================
async def list_logs(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 50,
    level: str | None = None,
    category: str | None = None,
    task_id: int | None = None,
) -> tuple[list[SystemLog], int]:
    statement: Select = select(SystemLog)
    count_statement = select(func.count()).select_from(SystemLog)
    conditions = []
    if level:
        conditions.append(SystemLog.level == level)
    if category:
        conditions.append(SystemLog.category == category)
    if task_id is not None:
        conditions.append(SystemLog.task_id == task_id)
    for condition in conditions:
        statement = statement.where(condition)
        count_statement = count_statement.where(condition)

    total = int((await db.execute(count_statement)).scalar() or 0)
    statement = (
        statement.order_by(SystemLog.created_at.desc(), SystemLog.id.desc())
        .offset(max(0, (page - 1) * page_size))
        .limit(page_size)
    )
    return list((await db.execute(statement)).scalars().all()), total


async def purge_logs(db: AsyncSession, keep_days: int = 30) -> int:
    cutoff = utcnow().replace(tzinfo=None) - timedelta(days=keep_days)
    result = await db.execute(delete(SystemLog).where(SystemLog.created_at < cutoff))
    await db.commit()
    return int(result.rowcount or 0)


def task_is_active(task: ScrapeTask) -> bool:
    return task.status in {TaskStatus.PENDING.value, TaskStatus.RUNNING.value}


def task_created_naive(task: ScrapeTask) -> datetime | None:
    return to_naive_utc(task.created_at)
