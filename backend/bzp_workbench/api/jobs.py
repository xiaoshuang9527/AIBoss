"""岗位结果路由：列表、详情、筛选面板、收藏、技能词频。"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..schemas import JobFacets, JobFacet, JobRead, TopSkill
from ..services import repository as repo
from .helpers import ok, page

router = APIRouter(prefix="/jobs", tags=["岗位结果"])


@router.get("")
async def list_jobs(
    db: AsyncSession = Depends(get_db),
    page_number: int = Query(1, alias="page", ge=1),
    page_size: int = Query(20, ge=1, le=100),
    order_by: str = Query("created_desc"),
    keyword: str | None = None,
    city: str | None = None,
    task_id: int | None = None,
    experience: str | None = None,
    degree: str | None = None,
    company_scale: str | None = None,
    salary_min: int | None = Query(None, ge=0, le=500),
    salary_max: int | None = Query(None, ge=0, le=500),
    skill: str | None = None,
    only_new: bool | None = None,
    only_favorite: bool | None = None,
    only_with_jd: bool | None = None,
):
    """岗位列表（支持关键词、城市、薪资、经验、学历、技能等筛选）。"""
    rows, total = await repo.list_jobs(
        db,
        page=page_number,
        page_size=page_size,
        order_by=order_by,
        keyword=keyword,
        city=city,
        task_id=task_id,
        experience=experience,
        degree=degree,
        company_scale=company_scale,
        salary_min=salary_min,
        salary_max=salary_max,
        skill=skill,
        only_new=only_new,
        only_favorite=only_favorite,
        only_with_jd=only_with_jd,
    )
    items = [JobRead.from_orm_job(row).model_dump() for row in rows]
    return ok(page(items, total, page_number, page_size).model_dump())


@router.get("/facets")
async def job_facets(db: AsyncSession = Depends(get_db)):
    """筛选面板的可选值（城市、经验、学历、技能、薪资区间）。"""
    data = await repo.job_facets(db)
    return ok(
        JobFacets(
            cities=[JobFacet(**item) for item in data["cities"]],
            experiences=[JobFacet(**item) for item in data["experiences"]],
            degrees=[JobFacet(**item) for item in data["degrees"]],
            skills=[JobFacet(**item) for item in data["skills"]],
            salary_min=data["salary_min"],
            salary_max=data["salary_max"],
        ).model_dump()
    )


@router.get("/top-skills")
async def top_skills(limit: int = Query(15, ge=1, le=100), db: AsyncSession = Depends(get_db)):
    """全局技能词频排行（结果页的图表用）。"""
    rows = await repo.top_skills(db, limit=limit)
    return ok([TopSkill(skill=skill, count=count).model_dump() for skill, count in rows])


@router.get("/{job_id}")
async def get_job(job_id: int, db: AsyncSession = Depends(get_db)):
    """岗位详情（含岗位职责、岗位要求全文与 BOSS 跳转链接）。"""
    job = await repo.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="岗位不存在")
    return ok(JobRead.from_orm_job(job).model_dump())


@router.post("/{job_id}/favorite")
async def toggle_favorite(
    job_id: int,
    favorite: bool | None = Body(None, embed=True),
    db: AsyncSession = Depends(get_db),
):
    """收藏 / 取消收藏岗位。"""
    job = await repo.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="岗位不存在")
    target = (not job.is_favorite) if favorite is None else bool(favorite)
    job = await repo.set_favorite(db, job, target)
    return ok({"id": job.id, "is_favorite": job.is_favorite})


@router.delete("")
async def delete_jobs(ids: list[int] = Body(..., embed=True), db: AsyncSession = Depends(get_db)):
    """批量删除岗位。"""
    deleted = await repo.delete_jobs(db, ids)
    return ok({"deleted": deleted})
