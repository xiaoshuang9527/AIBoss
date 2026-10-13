"""对话级实时推送：把抓取任务的进度推回发起它的对话。

为什么需要单独一层
------------------
现有的 ``task_events`` 是**全局广播**：任何任务的状态变化都会发给所有连接。
但对话是分会话的 —— 「任务 #3 完成了」只应该出现在发起它的那个对话里，
否则用户开两个对话会同时收到不相干的任务消息。

关键问题：任务事件里只有 ``task_id``，没有 ``conversation_id``。
两种解法：

1. 在 ``queue_service`` 的 15 处 ``publish_task_event`` 调用里都带上会话 id
   —— 侵入性太大，且那些调用点大多拿不到会话上下文。
2. **在这里按 task_id 反查**（本模块采用）。

选 2 的理由：``ScrapeTask.conversation_id`` 一旦写入就不变，所以每个任务
**只需要查一次**，之后走内存缓存。中继本身也就与 queue_service 解耦了 ——
它不需要知道有哪些事件类型，原样转发即可。

设计取舍：这是**基于轮询的 WebSocket**（收到事件后查库补齐 payload），
而不是把完整 payload 推下去。好处是前端拿到的状态一定与数据库一致；
代价是每个进度事件多一次轻量查询 —— 进度事件本身有节流（几秒一次），可以接受。
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import WebSocket

from ...db import SessionLocal
from ...models import ScrapeTask
from ...utils import utcnow
from ..events import task_events
from ..logging_service import logger

#: 同一个任务的进度推送最小间隔（秒）。
#:
#: 爬虫的进度回调可能很密集。对话流里刷屏会打断阅读，而且前端每次都要更新，
#: 所以这里做节流：只保留最新状态。
PROGRESS_THROTTLE_SECONDS = 3.0

#: 会推送到对话的事件类型。其它事件（队列快照等）与对话无关，直接丢弃。
_FORWARDED_EVENTS = {
    "task.created",
    "task.started",
    "task.progress",
    "task.retrying",
    "task.finished",
    "task.updated",
}

#: 任务结束后清掉状态变化事件，避免继续占用缓存
_TERMINAL_EVENT = "task.finished"

#: 单个任务的状态缓存上限（防止缓存无限增长）
_MAX_TASK_CACHE = 512


class ChatEventHub:
    """按会话分发的 WebSocket 事件中心。"""

    def __init__(self) -> None:
        #: conversation_id -> 该会话下所有连接的事件队列
        self._subscribers: dict[int, set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()
        #: task_id -> conversation_id（含 None，表示"非对话发起"，也要缓存
        #: 以免反复查库）
        self._task_conversation: dict[int, int | None] = {}
        #: task_id -> 上次推送时间戳
        self._last_push: dict[int, float] = {}
        self._relay_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # 订阅管理
    # ------------------------------------------------------------------
    async def subscribe(self, conversation_id: int) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        async with self._lock:
            self._subscribers.setdefault(conversation_id, set()).add(queue)
        return queue

    async def unsubscribe(self, conversation_id: int, queue: asyncio.Queue) -> None:
        async with self._lock:
            buckets = self._subscribers.get(conversation_id)
            if buckets is not None:
                buckets.discard(queue)
                if not buckets:
                    self._subscribers.pop(conversation_id, None)

    @property
    def subscriber_count(self) -> int:
        return sum(len(bucket) for bucket in self._subscribers.values())

    # ------------------------------------------------------------------
    # 中继：订阅全局任务事件 → 按会话分发
    # ------------------------------------------------------------------
    def start_relay(self) -> None:
        """启动后台中继（由应用 lifespan 调用）。

        幂等：重复调用不会起第二个中继。
        """
        if self._relay_task is not None and not self._relay_task.done():
            return
        self._relay_task = asyncio.create_task(self._relay_loop())
        logger.info("对话事件中继已启动")

    async def stop_relay(self) -> None:
        if self._relay_task is None:
            return
        self._relay_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._relay_task
        self._relay_task = None

    async def _relay_loop(self) -> None:
        """持续消费全局任务事件并转发到对应会话。"""
        queue = await task_events.subscribe()
        try:
            while True:
                payload = await queue.get()
                try:
                    await self._handle(payload)
                except Exception:  # noqa: BLE001
                    # 中继绝不能因为单个事件出错而退出 —— 否则之后所有
                    # 会话都收不到推送，而且用户完全看不出问题在哪里
                    logger.exception("转发任务事件失败：%s", payload.get("type"))
        except asyncio.CancelledError:
            raise
        finally:
            await task_events.unsubscribe(queue)

    async def _handle(self, payload: dict[str, Any]) -> None:
        event_type = payload.get("type")
        if event_type not in _FORWARDED_EVENTS:
            return

        data = payload.get("data") or {}
        task_id = data.get("task_id")
        if task_id is None:
            return

        conversation_id = await self._resolve_conversation(task_id)
        logger.debug(
            "[中继] 事件=%s 任务=#%s 归属会话=%r 订阅者=%s",
            event_type,
            task_id,
            conversation_id,
            sorted(self._subscribers),
        )
        if conversation_id is None:
            # 不是从对话发起的任务（用户在「创建抓取任务」页发的），
            # 不推给任何对话
            return

        if not self._has_subscriber(conversation_id):
            # 没人在听就不必查库构造 payload，省一次查询
            if event_type == _TERMINAL_EVENT:
                self._forget(task_id)
            return

        if not self._should_push(task_id, event_type):
            return

        snapshot = await self._task_snapshot(task_id)
        if snapshot is None:
            self._forget(task_id)
            return

        await self._broadcast(
            conversation_id,
            {
                "type": "chat.task_progress",
                "ts": utcnow().isoformat(),
                "data": {
                    "event": event_type,
                    "conversation_id": conversation_id,
                    "task": snapshot,
                },
            },
        )

        if event_type == _TERMINAL_EVENT:
            self._forget(task_id)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _has_subscriber(self, conversation_id: int) -> bool:
        return bool(self._subscribers.get(conversation_id))

    def _should_push(self, task_id: int, event_type: str) -> bool:
        """节流：进度类事件至少间隔 N 秒；状态变化（started/finished）不节流。"""
        if event_type in ("task.started", "task.finished", "task.created"):
            self._last_push[task_id] = asyncio.get_running_loop().time()
            return True

        now = asyncio.get_running_loop().time()
        last = self._last_push.get(task_id, 0.0)
        if now - last < PROGRESS_THROTTLE_SECONDS:
            return False
        self._last_push[task_id] = now
        return True

    async def _resolve_conversation(self, task_id: int) -> int | None:
        """查任务属于哪个对话。

        ⚠️ **只缓存正向结果**（确实是某个对话发起的）。
        查不到时**不缓存**，下次重查。

        为什么不能缓存 None（踩过的坑）
        ------------------------------
        缓存 ``None`` 看起来能省一次查询，但它会**永久屏蔽**之后任何归属变更：
        如果任务在首次被查到归属之前还没有 ``conversation_id``（例如应用层
        先建任务、稍后再补写归属），缓存下来的 ``None`` 就再也不会刷新 ——
        表现为"任务明明归属这个对话，却永远收不到推送"，而且日志里
        归属会话=None，极容易误判成路由逻辑写错。

        任务号被复用时（测试反复建删、数据库重置）问题更明显。

        代价：非对话发起的任务每次事件都多一次轻量查询。这类任务本来就少，
        而且事件有节流，可以接受 —— 正确性比这点开销重要。
        """
        cached = self._task_conversation.get(task_id)
        if cached is not None:
            return cached

        conversation_id: int | None = None
        try:
            async with SessionLocal() as session:
                task = await session.get(ScrapeTask, task_id)
                if task is not None:
                    conversation_id = task.conversation_id
        except Exception:  # noqa: BLE001
            logger.exception("查询任务 %s 的会话失败", task_id)
            return None

        if conversation_id is None:
            # 不缓存负结果（见上面的说明）
            return None

        if len(self._task_conversation) >= _MAX_TASK_CACHE:
            # 简单粗暴地清掉一半，避免无界增长；这个缓存丢了只是多查几次库
            for key in list(self._task_conversation)[: _MAX_TASK_CACHE // 2]:
                self._task_conversation.pop(key, None)

        self._task_conversation[task_id] = conversation_id
        return conversation_id

    def _forget(self, task_id: int) -> None:
        self._task_conversation.pop(task_id, None)
        self._last_push.pop(task_id, None)

    async def _task_snapshot(self, task_id: int) -> dict[str, Any] | None:
        """取任务的最新状态。

        刻意在收到事件时**重新查库**而不是信任事件里的 payload：
        事件是"发生了什么"的通知，而我们要推给前端的是"现在是什么状态"。
        两者在节流场景下会不一致（中间的事件被丢掉了）。
        """
        async with SessionLocal() as session:
            task = await session.get(ScrapeTask, task_id)
            if task is None:
                return None
            return {
                "task_id": task.id,
                "keyword": task.keyword,
                "city": task.city,
                "status": task.status,
                "status_label": _status_label(task.status),
                "is_active": task.status in ("pending", "running"),
                "progress": task.progress or 0,
                "stage_text": task.stage_text,
                "total_found": task.total_found or 0,
                "new_count": task.new_count or 0,
                "detail_count": task.detail_count or 0,
                "error_message": task.error_message,
                "duration_seconds": task.duration_seconds,
                "finished_at": task.finished_at.isoformat() if task.finished_at else None,
            }

    async def _broadcast(self, conversation_id: int, payload: dict[str, Any]) -> None:
        """发给某个会话下的所有连接。"""
        buckets = list(self._subscribers.get(conversation_id, ()))
        for queue in buckets:
            if queue.full():
                # 慢客户端：丢最旧的，保证它还能看到最新状态
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(payload)

    # ------------------------------------------------------------------
    # WebSocket 端点
    # ------------------------------------------------------------------
    async def handle(self, websocket: WebSocket, conversation_id: int) -> None:
        """处理一个对话的 WebSocket 连接。"""
        await websocket.accept()
        queue = await self.subscribe(conversation_id)
        try:
            await websocket.send_json(
                {
                    "type": "connected",
                    "data": {"channel": f"chat:{conversation_id}"},
                }
            )
            while True:
                payload = await queue.get()
                await websocket.send_json(payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            # 客户端断开会产生各种 WebSocket 异常，统一当正常断开处理
            pass
        finally:
            await self.unsubscribe(conversation_id, queue)
            with contextlib.suppress(Exception):
                await websocket.close()


def _status_label(status: str) -> str:
    """状态 → 中文标签，复用枚举自带的定义避免两份映射不同步。"""
    from ...enums import TaskStatus

    try:
        return TaskStatus(status).label
    except ValueError:
        return status


#: 全局单例
chat_events = ChatEventHub()


__all__ = ["ChatEventHub", "chat_events"]
