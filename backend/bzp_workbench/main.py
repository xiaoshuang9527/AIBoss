"""FastAPI 应用入口。

启动顺序：
1. 建目录、配置日志
2. 建表（SQLite 开箱即用；生产建议用 Alembic，见 deploy/sql/）
3. 应用运行时设置覆盖
4. 拉起任务队列工作协程 + 定时维护任务
"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import auth as auth_router
from .api import chat as chat_router
from .api import export as export_router
from .api import jobs as jobs_router
from .api import models as models_router
from .api import system as system_router
from .api import tasks as tasks_router
from .api import ws as ws_router
from .api.system import apply_runtime_overrides
from .config import settings
from .db import SessionLocal, dispose_db, init_db
from .enums import LogCategory, LogLevel
from .services import repository as repo
from .services.auth_service import ensure_default_profile
from .services.ai.chat_events import chat_events
from .services.logging_service import configure_logging, log_event
from .services.queue_service import scan_stale_tasks, task_queue


async def _maintenance_loop() -> None:
    """周期性维护：扫描卡死任务 + 过期日志清理。"""
    while True:
        await asyncio.sleep(max(30, settings.stale_task_scan_seconds))
        try:
            fixed = await scan_stale_tasks()
            if fixed:
                await log_event(
                    None,
                    f"维护任务：修正 {fixed} 个卡死任务状态",
                    level=LogLevel.WARNING,
                    category=LogCategory.SYSTEM,
                )
            async with SessionLocal() as db:
                await repo.purge_logs(db, keep_days=30)
        except asyncio.CancelledError:
            raise
        except Exception:
            # 维护任务失败不能影响主服务
            continue


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings.ensure_directories()
    apply_runtime_overrides()

    await init_db()

    async with SessionLocal() as db:
        await ensure_default_profile(db)

    await task_queue.start()
    # 启动对话事件中继：把全局任务事件按会话分发（P3）
    chat_events.start_relay()
    maintenance = asyncio.create_task(_maintenance_loop(), name="bzp-maintenance")

    async with SessionLocal() as db:
        await log_event(
            db,
            f"工作台后端启动完成（{settings.app_name} v{settings.app_version}）",
            category=LogCategory.SYSTEM,
        )

    try:
        yield
    finally:
        maintenance.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await maintenance
        await chat_events.stop_relay()
        await task_queue.stop()
        await dispose_db()


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "个人求职辅助工作台后端：封装 boss-zhipin-scraper，"
            "提供岗位抓取任务的创建、调度、结果查询与导出。"
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---------- 统一错误响应，前端只需要判断 code ----------
    @application.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        first = errors[0] if errors else {}
        location = ".".join(str(item) for item in first.get("loc", []) if item != "body")
        message = f"参数校验失败：{location} {first.get('msg', '')}".strip()
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "message": message,
                # errors 里可能带 ValueError 等非 JSON 原生对象，统一编码
                "detail": jsonable_encoder(errors, custom_encoder={Exception: str}),
            },
        )

    @application.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        from .services.logging_service import logger

        logger.exception("未处理异常: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"code": 500, "message": f"服务内部错误：{type(exc).__name__}"},
        )

    # ---------- 路由 ----------
    prefix = settings.api_prefix
    application.include_router(system_router.router, prefix=prefix)
    application.include_router(models_router.router, prefix=prefix)
    application.include_router(chat_router.router, prefix=prefix)
    application.include_router(auth_router.router, prefix=prefix)
    application.include_router(tasks_router.router, prefix=prefix)
    application.include_router(jobs_router.router, prefix=prefix)
    application.include_router(export_router.router, prefix=prefix)
    application.include_router(ws_router.router, prefix=prefix)

    @application.get("/", tags=["元信息"])
    async def root():
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
            "api_prefix": prefix,
            "disclaimer": settings.disclaimer,
        }

    return application


app = create_app()
