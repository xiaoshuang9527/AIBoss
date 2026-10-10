"""导出路由：CSV / JSON 下载。"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..enums import LogCategory
from ..services import repository as repo
from ..services.export_service import build_filename, jobs_to_csv, jobs_to_json
from ..services.logging_service import log_event
from .helpers import ok

router = APIRouter(prefix="/export", tags=["导出"])


def _content_disposition(filename: str) -> str:
    """兼容中文文件名（RFC 5987）。"""
    return f"attachment; filename*=UTF-8''{quote(filename)}"


@router.get("/jobs")
async def export_jobs(
    db: AsyncSession = Depends(get_db),
    fmt: str = Query("csv", pattern="^(csv|json)$"),
    task_id: int | None = None,
    keyword: str | None = None,
    city: str | None = None,
    include_jd: bool = True,
    limit: int = Query(5000, ge=1, le=50000),
):
    """按条件导出岗位数据。"""
    jobs = await repo.iterate_jobs(
        db,
        task_id=task_id,
        keyword=keyword,
        city=city,
    )
    jobs = list(jobs)[:limit]
    if not jobs:
        raise HTTPException(status_code=404, detail="没有符合条件的岗位数据")

    label_parts = ["岗位"]
    if keyword:
        label_parts.append(keyword)
    if city:
        label_parts.append(city)
    filename = build_filename("_".join(label_parts), fmt)

    if fmt == "json":
        body = jobs_to_json(jobs, include_jd=include_jd)
        media_type = "application/json; charset=utf-8"
    else:
        body = jobs_to_csv(jobs, include_jd=include_jd)
        media_type = "text/csv; charset=utf-8"

    await log_event(
        db,
        f"导出 {len(jobs)} 条岗位数据（{fmt}）",
        category=LogCategory.EXPORT,
        detail=f"task_id={task_id} keyword={keyword} city={city}",
    )

    return Response(
        content=body.encode("utf-8"),
        media_type=media_type,
        headers={
            "Content-Disposition": _content_disposition(filename),
            "X-Export-Count": str(len(jobs)),
        },
    )


@router.post("/preview")
async def export_preview(
    db: AsyncSession = Depends(get_db),
    task_id: int | None = None,
    keyword: str | None = None,
    city: str | None = None,
    limit: int = Query(200, ge=1, le=2000),
):
    """导出前预览：返回可导出的条数，避免用户点了才报 404。"""
    jobs = await repo.iterate_jobs(db, task_id=task_id, keyword=keyword, city=city)
    total = len(jobs)
    return ok(
        {
            "total": total,
            "preview": [
                {"title": job.title, "company": job.company, "salary": job.salary}
                for job in list(jobs)[:limit]
            ],
        }
    )
