"""WebSocket 实时通道。

* ``/ws/tasks`` —— 任务进度、队列状态、任务完成事件
* ``/ws/auth``  —— 扫码授权进度

前端优先用 WebSocket，断线时自动降级为 HTTP 轮询。
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket

from ..services.events import auth_events, task_events

router = APIRouter(tags=["实时通道"])


@router.websocket("/ws/tasks")
async def tasks_channel(websocket: WebSocket) -> None:
    await task_events.handle(websocket)


@router.websocket("/ws/auth")
async def auth_channel(websocket: WebSocket) -> None:
    await auth_events.handle(websocket)
