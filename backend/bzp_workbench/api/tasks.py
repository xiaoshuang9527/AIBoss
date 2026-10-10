"""抓取任务路由：创建、列表、详情、日志、取消、重试、删除。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db
from ..enums import LogCategory, TaskStatus
from ..models import AuthProfile
from ..schemas import QueueStatus, TaskCreate, TaskLogRead, TaskRead
from ..services import auth_service
from ..services import repository as repo
from ..services.events import publish_task_event
from ..services.logging_service import log_event
from ..services.queue_service import task_queue
from ..utils import utcnow
from .helpers import ok, page

router = APIRouter(prefix="/tasks", tags=["抓取任务"])


@router.post("")
async def create_task(payload: TaskCreate, db: AsyncSession = Depends(get_db)):
    """提交抓取任务（进入后台队列）。"""
    if payload.auth_profile_id:
        profile = await db.get(AuthProfile, payload.auth_profile_id)
    else:
        profile = await auth_service.get_default_or_create(db)
    if profile is None:
        raise HTTPException(status_code=404, detail="授权档案不存在")

    task = await repo.create_task(
        db,
        {
            "keyword": payload.keyword,
            "city": payload.city,
            "salary": payload.salary,
            "experience": payload.experience,
            "degree": payload.degree,
            "company_scale": payload.company_scale,
            "company_stage": payload.company_stage,
            "industry": payload.industry,
            "max_pages": payload.max_pages,
            "with_detail": payload.with_detail,
            "note": payload.note,
            "auth_profile_id": profile.id,
            "status": TaskStatus.PENDING.value,
            "stage_text": "已入队，等待执行",
        },
    )

    await log_event(
        db,
        f"创建抓取任务 #{task.id}：{task.keyword} @ {task.city}（{task.max_pages} 页）",
        category=LogCategory.TASK,
        task_id=task.id,
    )
    task_queue.notify()
    publish_task_event("task.created", {"task_id": task.id, "status": task.status})

    snapshot = await task_queue.snapshot_with_pending()
    return ok(
        {
            "task": TaskRead.from_orm_task(task).model_dump(),
            "queue": snapshot.to_payload(),
        }
    )


@router.get("")
async def list_tasks(
    db: AsyncSession = Depends(get_db),
    page_number: int = Query(1, alias="page", ge=1),
    page_size: int = Query(20, ge=1, le=200),
    status: str | None = None,
    keyword: str | None = None,
    city: str | None = None,
):
    """分页查询任务（历史记录页 / 最近任务表都用它）。"""
    rows, total = await repo.list_tasks(
        db, page=page_number, page_size=page_size, status=status, keyword=keyword, city=city
    )
    items = [TaskRead.from_orm_task(row).model_dump() for row in rows]
    return ok(page(items, total, page_number, page_size).model_dump())


@router.get("/queue")
async def queue_status():
    """队列实时状态。"""
    snapshot = await task_queue.snapshot_with_pending()
    return ok(QueueStatus(**snapshot.to_payload()).model_dump())


@router.get("/{task_id}")
async def get_task(task_id: int, db: AsyncSession = Depends(get_db)):
    task = await repo.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    payload = TaskRead.from_orm_task(task).model_dump()
    payload["queue_running"] = task_queue.is_running(task_id)
    return ok(payload)


@router.get("/{task_id}/logs")
async def get_task_logs(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(300, ge=1, le=2000),
):
    """读取任务运行日志尾部（实时刷新用）。"""
    task = await repo.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    from ..services.queue_service import task_log_tail

    lines, total = task_log_tail(task_id, limit=limit)
    return ok(
        TaskLogRead(
            task_id=task_id,
            status=task.status,
            progress=task.progress,
            stage_text=task.stage_text,
            lines=lines,
            total_lines=total,
        ).model_dump()
    )


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: int, db: AsyncSession = Depends(get_db)):
    """取消任务：排队中的直接标记取消，运行中的终止爬虫子进程。"""
    task = await repo.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status not in (TaskStatus.PENDING.value, TaskStatus.RUNNING.value):
        raise HTTPException(status_code=409, detail="任务已结束，无法取消")

    terminated = await task_queue.request_cancel(task_id)
    if not terminated:
        await repo.update_task(
            db,
            task,
            status=TaskStatus.CANCELLED.value,
            stage_text="已取消",
            finished_at=utcnow().replace(tzinfo=None),
        )
        publish_task_event(
            "task.finished", {"task_id": task_id, "status": TaskStatus.CANCELLED.value}
        )
    return ok({"cancelled": True, "terminated_process": terminated})


@router.post("/{task_id}/retry")
async def retry_task(task_id: int, db: AsyncSession = Depends(get_db)):
    """重试任务：重置状态重新入队。"""
    task = await repo.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status in (TaskStatus.PENDING.value, TaskStatus.RUNNING.value):
        raise HTTPException(status_code=409, detail="任务正在执行中")

    await repo.update_task(
        db,
        task,
        status=TaskStatus.PENDING.value,
        progress=0,
        stage_text="已重新入队",
        error_message=None,
        attempts=0,
        started_at=None,
        finished_at=None,
        duration_seconds=None,
    )
    await log_event(
        db, f"重试任务 #{task.id}", category=LogCategory.TASK, task_id=task.id
    )
    task_queue.notify()
    publish_task_event("task.created", {"task_id": task.id, "status": task.status})
    snapshot = await task_queue.snapshot_with_pending()
    return ok({"task": TaskRead.from_orm_task(task).model_dump(), "queue": snapshot.to_payload()})


@router.delete("/{task_id}")
async def delete_task(task_id: int, db: AsyncSession = Depends(get_db)):
    """删除任务记录（岗位数据保留，它是全局去重的资产）。"""
    task = await repo.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status == TaskStatus.RUNNING.value:
        raise HTTPException(status_code=409, detail="任务正在执行，请先取消")
    await repo.delete_task(db, task)
    return ok({"deleted": True})


@router.get("/{task_id}/summary")
async def task_summary(task_id: int, db: AsyncSession = Depends(get_db)):
    """任务级聚合统计：薪资分布、技能词频、公司分布。"""
    task = await repo.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    from ..parsing import SALARY_BUCKET_ORDER, salary_bucket
    from collections import Counter

    jobs, total = await repo.list_jobs(db, page=1, page_size=1000, task_id=task_id)

    salary_counter: Counter[str] = Counter()
    company_counter: Counter[str] = Counter()
    skill_counter: Counter[str] = Counter()
    import json

    for job in jobs:
        salary_counter[salary_bucket(job.salary_avg)] += 1
        if job.company:
            company_counter[job.company] += 1
        if job.skill_keywords:
            try:
                for skill in set(json.loads(job.skill_keywords)):
                    skill_counter[str(skill)] += 1
            except (TypeError, ValueError):
                continue

    return ok(
        {
            "task_id": task_id,
            "keyword": task.keyword,
            "city": task.city,
            "total_jobs": total,
            "salary_distribution": [
                {"bucket": bucket, "count": salary_counter.get(bucket, 0)}
                for bucket in SALARY_BUCKET_ORDER
                if salary_counter.get(bucket, 0) > 0
            ],
            "top_companies": [
                {"value": name, "count": count} for name, count in company_counter.most_common(10)
            ],
            "top_skills": [
                {"skill": name, "count": count} for name, count in skill_counter.most_common(15)
            ],
            "max_pages": task.max_pages,
            "with_detail": task.with_detail,
            "recommended_delay": [settings.task_delay_min, settings.task_delay_max],
        }
    )
