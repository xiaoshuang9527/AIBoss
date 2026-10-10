"""账号授权路由。

对应左侧导航「账号授权」：管理 BOSS 直聘登录态。

流程：``POST /auth/sessions`` 启动一个专用浏览器 → 用户窗口内扫码 →
后台自动校验并加密保存登录态 → 前端通过 ``/auth/sessions/current``
或 WebSocket ``/ws/auth`` 实时看到进度。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..enums import AuthStatus
from ..models import AuthProfile
from ..schemas import (
    AuthProfileRead,
    AuthSessionRead,
    AuthStartRequest,
    AuthVerifyResult,
)
from ..services import auth_service
from ..services.auth_service import login_manager
from .helpers import ok

router = APIRouter(prefix="/auth", tags=["账号授权"])


@router.get("/profiles")
async def list_profiles(db: AsyncSession = Depends(get_db)):
    """列出所有授权档案并确保默认档案存在。"""
    await auth_service.ensure_default_profile(db)
    rows = (
        await db.execute(select(AuthProfile).order_by(AuthProfile.is_default.desc(), AuthProfile.id))
    ).scalars().all()
    return ok([AuthProfileRead.from_orm_profile(row).model_dump() for row in rows])


@router.get("/status")
async def auth_status(db: AsyncSession = Depends(get_db)):
    """当前授权状态（供顶部状态栏与概览卡片使用）。"""
    profile = await auth_service.get_default_or_create(db)
    profile_read = AuthProfileRead.from_orm_profile(profile)
    return ok(
        {
            "profile": profile_read.model_dump(),
            "authorized": profile.status == AuthStatus.ACTIVE.value,
            "authorizing": login_manager.is_busy(),
            "cdp_reachable": auth_service.cdp_reachable(profile.cdp_port),
        }
    )


@router.post("/verify")
async def verify_auth(db: AsyncSession = Depends(get_db)):
    """重新校验登录态（跑一次爬虫的 --check）。"""
    profile = await auth_service.get_default_or_create(db)
    if login_manager.is_busy():
        raise HTTPException(status_code=409, detail="正在授权中，请先完成或取消当前授权")
    profile = await auth_service.refresh_profile_status(db, profile)
    outcome = {
        "ok": profile.status == AuthStatus.ACTIVE.value,
        "status": profile.status,
        "status_label": auth_service.profile_status_label(profile.status),
        "message": "登录态有效" if profile.status == AuthStatus.ACTIVE.value else "登录态无效",
        "detail": profile.last_error,
    }
    return ok(AuthVerifyResult(**outcome).model_dump())


@router.post("/sessions")
async def start_auth_session(payload: AuthStartRequest, db: AsyncSession = Depends(get_db)):
    """启动扫码授权会话。"""
    try:
        session = await login_manager.start(
            db,
            browser=payload.browser,
            copy_login_state=payload.copy_login_state,
            reset_profile=payload.reset_profile,
            name=payload.name,
            external_browser=payload.external_browser,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ok(AuthSessionRead(**session.to_payload()).model_dump())


@router.get("/sessions/current")
async def current_auth_session():
    """查询当前授权会话进度（前端轮询兜底，WebSocket 为主）。"""
    session = login_manager.current
    if session is None:
        return ok(None)
    return ok(AuthSessionRead(**session.to_payload()).model_dump())


@router.delete("/sessions/current")
async def cancel_auth_session():
    """取消当前授权会话。"""
    cancelled = await login_manager.cancel()
    return ok({"cancelled": cancelled})


@router.post("/logout")
async def logout(db: AsyncSession = Depends(get_db)):
    """停用登录态（保留 profile 文件，仅把状态标记为失效）。"""
    profile = await auth_service.get_default_or_create(db)
    profile.status = AuthStatus.EXPIRED.value
    profile.last_error = "用户手动停用登录态"
    await db.commit()
    return ok({"status": profile.status})
