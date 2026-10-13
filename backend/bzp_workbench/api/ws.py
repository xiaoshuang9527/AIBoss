"""WebSocket 实时通道。

* ``/ws/tasks``            —— 任务进度、队列状态、任务完成事件（全局）
* ``/ws/auth``             —— 扫码授权进度
* ``/ws/chat/{会话 id}``   —— 该对话发起的任务进度（P3）

``/ws/chat`` 与 ``/ws/tasks`` 的区别：后者是全局广播，任何任务变化都会推送；
前者只推**属于该对话**的任务。用户可能开着多个对话，全局广播会让他在每个
对话里都看到不相干的任务消息。

前端优先用 WebSocket，断线时自动降级为 HTTP 轮询。
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket

from ..services.ai.chat_events import chat_events
from ..services.events import auth_events, task_events

router = APIRouter(tags=["实时通道"])


@router.websocket("/ws/tasks")
async def tasks_channel(websocket: WebSocket) -> None:
    await task_events.handle(websocket)


@router.websocket("/ws/auth")
async def auth_channel(websocket: WebSocket) -> None:
    await auth_events.handle(websocket)


@router.websocket("/ws/chat/{conversation_id}")
async def chat_channel(websocket: WebSocket, conversation_id: int) -> None:
    """某个对话的任务进度通道。"""
    await chat_events.handle(websocket, conversation_id)
