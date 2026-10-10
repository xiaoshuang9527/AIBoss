"""异步任务队列 + 并发控制 + 反爬节奏控制。

为什么不用 Celery
-----------------
抓取任务的核心是「启动一个浏览器 CDP 子进程」，这属于 I/O 等待而不是 CPU
密集，``asyncio`` + 固定大小的工作槽已经足够，且不需要额外部署 broker。
如果后续要多机横向扩展，把 :class:`TaskQueue` 的方法换成 Celery
``send_task`` 即可，路由层不用动。

并发与风控
----------
* 最多 ``BZP_MAX_CONCURRENT_TASKS`` 个任务同时执行（默认 2，硬上限 4）。
* 任务开始之间插入 ``[BZP_TASK_DELAY_MIN, BZP_TASK_DELAY_MAX]`` 的随机延迟。
* 登录态失效 / 触发风控的任务**不自动重试**，直接标记为「待处理」，
  等用户重新授权，避免重试把账号推向更严的限制。
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import SessionLocal
from ..enums import AuthStatus, LogCategory, LogLevel, TaskStatus
from ..models import JobRecord, ScrapeTask
from ..utils import utcnow
from . import repository as repo
from .events import publish_task_event
from .logging_service import log_event, task_log_path
from .scraper_runner import (
    REASON_LOGIN,
    REASON_RISK,
    REASON_TEXT,
    ScrapeRunResult,
    build_environment,
    cdp_reachable,
    merge_jobs_with_details,
    read_task_log,
    run_scrape,
)


@dataclass
class QueueSnapshot:
    running: int = 0
    pending: int = 0
    max_concurrent: int = 0
    worker_alive: bool = False
    running_task_ids: list[int] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "pending": self.pending,
            "max_concurrent": self.max_concurrent,
            "slots_free": max(0, self.max_concurrent - self.running),
            "worker_alive": self.worker_alive,
            "running_task_ids": self.running_task_ids,
        }


class TaskQueue:
    """单一后台工作协程驱动的任务队列。"""

    def __init__(self) -> None:
        self._worker: asyncio.Task | None = None
        self._wakeup = asyncio.Event()
        self._shutdown = asyncio.Event()
        self._running: dict[int, asyncio.Task] = {}
        self._processes: dict[int, asyncio.subprocess.Process] = {}
        self._cancel_requested: set[int] = set()
        self._last_start_at: float = 0.0
        self._idle_scans = 0

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._worker is not None and not self._worker.done():
            return
        self._shutdown.clear()
        await self._recover_pending()
        self._worker = asyncio.create_task(self._run_worker(), name="bzp-task-queue")
        publish_task_event("queue.started", self.snapshot().to_payload())

    async def stop(self) -> None:
        self._shutdown.set()
        self._wakeup.set()
        for process in list(self._processes.values()):
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError, OSError):
                    process.terminate()
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
        for task in list(self._running.values()):
            task.cancel()
        self._running.clear()
        self._processes.clear()

    async def _recover_pending(self) -> None:
        """启动时修正「上次进程被杀导致状态卡住」的任务。"""
        async with SessionLocal() as db:
            stuck = await repo.running_tasks(db)
            for task in stuck:
                task.status = TaskStatus.PENDING.value
                task.progress = 0
                task.stage_text = "服务重启，重新排队"
            if stuck:
                await db.commit()
                publish_task_event("tasks.requeued", {"task_ids": [t.id for t in stuck]})

    # ------------------------------------------------------------------
    # 入队 / 取消
    # ------------------------------------------------------------------
    def notify(self) -> None:
        """有新任务入队时唤醒工作协程。"""
        self._wakeup.set()

    def register_process(self, task_id: int, process: asyncio.subprocess.Process) -> None:
        self._processes[task_id] = process

    def unregister_process(self, task_id: int) -> None:
        self._processes.pop(task_id, None)

    async def request_cancel(self, task_id: int) -> bool:
        """请求取消任务：排队中的直接标取消，运行中的终止子进程。"""
        self._cancel_requested.add(task_id)
        process = self._processes.get(task_id)
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError, OSError):
                process.terminate()
            return True
        runner = self._running.get(task_id)
        if runner is not None:
            runner.cancel()
            return True
        return False

    def is_running(self, task_id: int) -> bool:
        return task_id in self._running

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    def snapshot(self) -> QueueSnapshot:
        return QueueSnapshot(
            running=len(self._running),
            max_concurrent=settings.max_concurrent_tasks,
            worker_alive=self._worker is not None and not self._worker.done(),
            running_task_ids=sorted(self._running),
        )

    async def snapshot_with_pending(self) -> QueueSnapshot:
        snapshot = self.snapshot()
        async with SessionLocal() as db:
            counts = await repo.count_tasks_by_status(db)
        snapshot.pending = counts.get(TaskStatus.PENDING.value, 0)
        return snapshot

    # ------------------------------------------------------------------
    # 工作循环
    # ------------------------------------------------------------------
    async def _run_worker(self) -> None:
        while not self._shutdown.is_set():
            try:
                dispatched = await self._dispatch_available()
                if dispatched:
                    continue
                self._wakeup.clear()
                try:
                    await asyncio.wait_for(self._wakeup.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    # 周期性兜底扫描：防止漏掉「直接写库」产生的待执行任务
                    self._idle_scans += 1
                    if self._idle_scans % 5 == 0:
                        self._wakeup.set()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 工作循环必须活下去
                publish_task_event("queue.error", {"message": str(exc)})
                await asyncio.sleep(2)

    async def _dispatch_available(self) -> bool:
        """尽量把空闲槽位填满，返回是否派发过任务。"""
        free = settings.max_concurrent_tasks - len(self._running)
        if free <= 0:
            return False

        async with SessionLocal() as db:
            candidates = await repo.claim_pending_tasks(db, limit=free)
            claimed: list[int] = []
            for task in candidates:
                task.status = TaskStatus.RUNNING.value
                task.started_at = utcnow().replace(tzinfo=None)
                task.progress = max(task.progress, 1)
                task.stage_text = "已分配执行槽，正在准备"
                task.attempts = task.attempts + 1
                claimed.append(task.id)
            if claimed:
                await db.commit()

        if not claimed:
            return False

        for task_id in claimed:
            await self._apply_start_delay()
            runner = asyncio.create_task(self._execute(task_id), name=f"bzp-task-{task_id}")
            self._running[task_id] = runner
            runner.add_done_callback(lambda _t, tid=task_id: self._on_runner_done(tid))
            publish_task_event("task.started", {"task_id": task_id})
        self._broadcast_queue()
        return True

    def _on_runner_done(self, task_id: int) -> None:
        self._running.pop(task_id, None)
        self._processes.pop(task_id, None)
        self._broadcast_queue()
        # 有槽位空出来，立刻尝试派发下一个
        self._wakeup.set()

    async def _apply_start_delay(self) -> None:
        """任务之间的随机延迟，降低触发风控的概率。"""
        if self._last_start_at <= 0:
            self._last_start_at = time.monotonic()
            return
        low = max(0.0, float(settings.task_delay_min))
        high = max(low, float(settings.task_delay_max))
        delay = random.uniform(low, high) if high > 0 else 0.0
        if delay <= 0:
            return
        publish_task_event("queue.delay", {"seconds": round(delay, 1)})
        await asyncio.sleep(delay)
        self._last_start_at = time.monotonic()

    def _broadcast_queue(self) -> None:
        publish_task_event("queue.updated", self.snapshot().to_payload())

    # ------------------------------------------------------------------
    # 单任务执行
    # ------------------------------------------------------------------
    async def _execute(self, task_id: int) -> None:
        """带重试的任务执行。"""
        attempt = 0
        try:
            while True:
                attempt += 1
                result = await self._execute_once(task_id, attempt)
                if result is None:
                    return  # 已取消

                if result.ok:
                    return

                retryable = result.error_reason not in (REASON_LOGIN, REASON_RISK)
                can_retry = attempt <= settings.max_retries and retryable
                if not can_retry:
                    await self._finalize_failure(task_id, result)
                    return

                await self._mark_retrying(task_id, attempt, result)
                await asyncio.sleep(min(30, 5 * attempt))
        except asyncio.CancelledError:
            await self._finalize_cancelled(task_id)
            raise
        except Exception as exc:  # 兜底，避免任务永久卡在 running
            await self._finalize_unexpected(task_id, exc)

    async def _execute_once(self, task_id: int, attempt: int) -> ScrapeRunResult | None:
        async with SessionLocal() as db:
            task = await repo.get_task(db, task_id)
            if task is None:
                return None
            if task.status == TaskStatus.CANCELLED.value or task_id in self._cancel_requested:
                return None
            if task.status != TaskStatus.RUNNING.value:
                # 被外部改状态（例如用户取消）
                return None

            profile = None
            if task.auth_profile_id:
                from ..models import AuthProfile

                profile = await db.get(AuthProfile, task.auth_profile_id)
            if profile is None:
                from .auth_service import get_default_or_create

                profile = await get_default_or_create(db)

            task.auth_profile_id = profile.id
            task.stage_text = f"准备抓取（第 {attempt} 次尝试）"
            task.progress = max(task.progress, 2)
            await db.commit()

            payload = {
                "keyword": task.keyword,
                "city": task.city,
                "salary": task.salary,
                "experience": task.experience,
                "degree": task.degree,
                "company_scale": task.company_scale,
                "company_stage": task.company_stage,
                "industry": task.industry,
                "max_pages": task.max_pages,
                "with_detail": task.with_detail,
                "profile_hash": profile.profile_hash,
                "cdp_port": profile.cdp_port or settings.cdp_port,
            }
            log_path = task_log_path(task_id)
            task.log_path = str(log_path)

            profile_status = profile.status
            profile_error = profile.last_error
            await db.commit()

        # CDP 端口没起时提前给出明确提示，别让用户等两分钟才看到「连接失败」
        if not cdp_reachable(payload["cdp_port"]):
            await self._try_autostart_browser(payload)

        progress_callback = self._make_progress_callback(task_id)

        result = await run_scrape(
            payload,
            task_id=task_id,
            log_path=log_path,
            on_progress=progress_callback,
            on_process=lambda process: self.register_process(task_id, process),
        )
        self.unregister_process(task_id)

        if task_id in self._cancel_requested:
            return None

        async with SessionLocal() as db:
            task = await repo.get_task(db, task_id)
            if task is None:
                return result

            if result.ok:
                await self._persist_success(db, task, result)
            else:
                await repo.update_task(
                    db,
                    task,
                    error_message=result.error_message,
                    stage_text=result.error_message,
                )
                # 登录态相关失败顺手把档案标过期，前端卡片会立刻变红
                if result.error_reason == REASON_LOGIN and task.auth_profile_id:
                    from ..models import AuthProfile

                    profile = await db.get(AuthProfile, task.auth_profile_id)
                    if profile is not None:
                        profile.status = AuthStatus.EXPIRED.value
                        profile.last_error = result.error_message
                        profile.updated_at = utcnow().replace(tzinfo=None)
                        await db.commit()
                await log_event(
                    db,
                    f"任务 #{task_id} 执行失败：{result.error_message}",
                    level=LogLevel.ERROR,
                    category=LogCategory.TASK,
                    task_id=task_id,
                    detail=result.error_reason,
                )
        return result

    async def _try_autostart_browser(self, payload: dict[str, Any]) -> None:
        """CDP 端口未监听时，在后台尝试拉起专用浏览器（已登录则直接成功）。

        这是「用户体验优化」而不是必需路径：真拉不起来时抓取会自己报出
        明确的 CDP 错误，所以这里只等有限的时间。
        """
        if not settings.autostart_browser:
            return

        env = build_environment(payload["profile_hash"], payload["cdp_port"])
        command = [
            env.python,
            str(env.entry),
            "--setup-chrome",
            "--cdp-port", str(payload["cdp_port"]),
            "--no-wait-login",
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(settings.scraper_path),
                env=env.env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except (OSError, FileNotFoundError):
            return

        publish_task_event("browser.starting", {"task_id": payload.get("task_id")})

        deadline = time.monotonic() + max(0, int(settings.browser_start_timeout_seconds))
        while time.monotonic() < deadline:
            if cdp_reachable(payload["cdp_port"]):
                return
            if process.returncode is not None:
                # 启动脚本自己退出了（例如已经登录），没必要继续等
                return
            await asyncio.sleep(0.5)

        with contextlib.suppress(ProcessLookupError, OSError):
            if process.returncode is None:
                process.terminate()

    def _make_progress_callback(self, task_id: int):
        async def callback(progress: int, stage: str) -> None:
            async with SessionLocal() as db:
                task = await repo.get_task(db, task_id)
                if task is None or task.status != TaskStatus.RUNNING.value:
                    return
                task.progress = max(task.progress, min(99, int(progress)))
                task.stage_text = stage
                await db.commit()
            publish_task_event(
                "task.progress",
                {"task_id": task_id, "progress": progress, "stage_text": stage, "status": "running"},
            )

        return callback

    async def _persist_success(
        self, db: AsyncSession, task: ScrapeTask, result: ScrapeRunResult
    ) -> None:
        """把抓取结果入库并完成任务。"""
        merged = merge_jobs_with_details(result.jobs, result.details)
        stats = await repo.sync_jobs(db, task, merged)

        await repo.update_task(
            db,
            task,
            status=TaskStatus.SUCCEEDED.value,
            progress=100,
            stage_text=f"完成：入库 {stats['inserted']} 条新岗位",
            total_found=len(merged),
            new_count=stats["inserted"],
            detail_count=result.detail_count,
            error_message=None,
            result_path=str(result.jobs_path) if result.jobs_path else None,
            duration_seconds=round(result.duration_seconds, 2),
            finished_at=utcnow().replace(tzinfo=None),
        )

        await log_event(
            db,
            f"任务 #{task.id} 抓取完成：命中 {len(merged)} 条，新增入库 {stats['inserted']} 条",
            category=LogCategory.TASK,
            task_id=task.id,
            detail=f"updated={stats['updated']} unchanged={stats['unchanged']} details={result.detail_count}",
        )
        publish_task_event(
            "task.finished",
            {
                "task_id": task.id,
                "status": TaskStatus.SUCCEEDED.value,
                "progress": 100,
                "total_found": len(merged),
                "new_count": stats["inserted"],
                "detail_count": result.detail_count,
            },
        )

    async def _mark_retrying(
        self, task_id: int, attempt: int, result: ScrapeRunResult
    ) -> None:
        async with SessionLocal() as db:
            task = await repo.get_task(db, task_id)
            if task is None:
                return
            await repo.update_task(
                db,
                task,
                stage_text=f"第 {attempt} 次失败，准备重试：{result.error_message}",
                progress=0,
            )
        publish_task_event(
            "task.retrying",
            {"task_id": task_id, "attempt": attempt, "reason": result.error_reason},
        )
        await log_event(
            None,
            f"任务 #{task_id} 第 {attempt} 次尝试失败，将重试：{result.error_message}",
            level=LogLevel.WARNING,
            category=LogCategory.TASK,
            task_id=task_id,
        )

    async def _finalize_failure(self, task_id: int, result: ScrapeRunResult) -> None:
        status = (
            TaskStatus.NEEDS_ATTENTION.value
            if result.error_reason in (REASON_LOGIN, REASON_RISK)
            else TaskStatus.FAILED.value
        )
        message = result.error_message or REASON_TEXT.get(result.error_reason or "", "抓取失败")
        async with SessionLocal() as db:
            task = await repo.get_task(db, task_id)
            if task is None:
                return
            await repo.update_task(
                db,
                task,
                status=status,
                stage_text=message,
                error_message=message,
                duration_seconds=round(result.duration_seconds, 2),
                total_found=len(result.jobs),
                detail_count=result.detail_count,
                finished_at=utcnow().replace(tzinfo=None),
            )
        publish_task_event(
            "task.finished",
            {
                "task_id": task_id,
                "status": status,
                "progress": task.progress if task else 0,
                "error_message": message,
                "reason": result.error_reason,
            },
        )

    async def _finalize_cancelled(self, task_id: int) -> None:
        async with SessionLocal() as db:
            task = await repo.get_task(db, task_id)
            if task is None:
                return
            if task.status in (TaskStatus.SUCCEEDED.value, TaskStatus.FAILED.value):
                return
            await repo.update_task(
                db,
                task,
                status=TaskStatus.CANCELLED.value,
                stage_text="已取消",
                finished_at=utcnow().replace(tzinfo=None),
            )
        self._cancel_requested.discard(task_id)
        publish_task_event(
            "task.finished", {"task_id": task_id, "status": TaskStatus.CANCELLED.value, "progress": 0}
        )

    async def _finalize_unexpected(self, task_id: int, exc: Exception) -> None:
        message = f"任务执行出现未预期错误：{type(exc).__name__}: {exc}"
        async with SessionLocal() as db:
            task = await repo.get_task(db, task_id)
            if task is not None:
                await repo.update_task(
                    db,
                    task,
                    status=TaskStatus.FAILED.value,
                    stage_text=message,
                    error_message=message,
                    finished_at=utcnow().replace(tzinfo=None),
                )
            await log_event(
                db,
                message,
                level=LogLevel.ERROR,
                category=LogCategory.TASK,
                task_id=task_id,
            )
        publish_task_event(
            "task.finished",
            {"task_id": task_id, "status": TaskStatus.FAILED.value, "error_message": message},
        )


# ============================================================
# 维护任务
# ============================================================
async def scan_stale_tasks() -> int:
    """把超时仍标记为运行中的任务收尾（进程被外部杀掉的情况）。"""
    fixed = 0
    async with SessionLocal() as db:
        stale = await repo.stale_running_tasks(db, settings.task_timeout_seconds + 300)
        for task in stale:
            task.status = TaskStatus.FAILED.value
            task.error_message = "任务长时间无进展，已判定为失败（服务可能被中断）"
            task.stage_text = task.error_message
            task.finished_at = utcnow().replace(tzinfo=None)
            fixed += 1
        if fixed:
            await db.commit()
    return fixed


def task_log_tail(task_id: int, limit: int = 300) -> tuple[list[str], int]:
    return read_task_log(task_id, limit=limit)


# 全局单例
task_queue = TaskQueue()
