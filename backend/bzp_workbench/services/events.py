"""WebSocket 事件广播。

前端用两条通道：

* ``/api/ws/tasks``    —— 任务进度、状态流转、队列变化
* ``/api/ws/auth``     —— 扫码授权进度

实现为「每个连接一个队列」的 fan-out，慢客户端直接丢事件而不是拖垮服务。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import WebSocket

from ..utils import utcnow


class EventBus:
    """进程内事件总线（单进程部署足够；多进程请换 Redis Pub/Sub）。"""

    def __init__(self, name: str, max_queue: int = 128) -> None:
        self.name = name
        self._max_queue = max_queue
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event_type: str, data: Any = None) -> None:
        """同步发布（可在非 async 上下文调用）。"""
        payload = {
            "type": event_type,
            "ts": utcnow().isoformat(),
            "data": data if data is not None else {},
        }
        for queue in list(self._subscribers):
            if queue.full():
                # 慢客户端：丢掉最旧的一条，保证它还能看到最新状态
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(payload)

    async def handle(self, websocket: WebSocket) -> None:
        """通用的 WebSocket 处理循环。"""
        await websocket.accept()
        queue = await self.subscribe()
        try:
            await websocket.send_text(
                json.dumps({"type": "connected", "data": {"channel": self.name}}, ensure_ascii=False)
            )
            while True:
                payload = await queue.get()
                await websocket.send_text(json.dumps(payload, ensure_ascii=False, default=str))
        except asyncio.CancelledError:
            raise
        except Exception:
            # 客户端断开会产生各种 WebSocket 异常，统一当作正常断开处理
            pass
        finally:
            await self.unsubscribe(queue)
            with contextlib.suppress(Exception):
                await websocket.close()


task_events = EventBus("tasks")
auth_events = EventBus("auth")


def publish_task_event(event_type: str, data: Any = None) -> None:
    task_events.publish(event_type, data)


def publish_auth_event(event_type: str, data: Any = None) -> None:
    auth_events.publish(event_type, data)
