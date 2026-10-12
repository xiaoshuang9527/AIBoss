"""AI 对话路由。

三个组成部分
------------
* **会话 CRUD** —— 新建、列表、历史消息、删除（**连带清理附件文件**）
* **附件上传** —— 按内容嗅探类型、大小校验、落盘并抽取文本
* **流式对话** —— SSE 逐帧下发（思考过程 / 正文 / 工具调用 / 用量）

关于错误处理：``StreamingResponse`` 一旦开始发送就无法再改 HTTP 状态码，
因此流内的失败一律转成 ``error`` 事件，而不是抛异常。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db
from ..enums import LogCategory
from ..models import ChatAttachment, ChatConversation, ChatMessage
from ..schemas import (
    ChatAttachmentRead,
    ChatConversationRead,
    ChatMessageRead,
    ChatSendRequest,
)
from ..services import ai
from ..services.ai import service as chat_service
from ..services.ai.router import MODES, UNSET_MODE
from ..services.logging_service import log_event, logger
from .helpers import ok

router = APIRouter(prefix="/chat", tags=["AI 对话"])


# ============================================================
# 转换
# ============================================================
def _loads(raw: str | None, default):
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return default
    return value


def to_attachment_read(row: ChatAttachment, *, include_text: bool = False) -> ChatAttachmentRead:
    data = ChatAttachmentRead.model_validate(row)
    data.size_label = _human_size(row.size_bytes)
    if not include_text:
        data.extracted_text = None
    return data


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def to_message_read(row: ChatMessage) -> ChatMessageRead:
    data = ChatMessageRead.model_validate(row)
    return data


# ============================================================
# 会话
# ============================================================
@router.get("/conversations")
async def list_conversations(
    db: AsyncSession = Depends(get_db), limit: int = Query(50, ge=1, le=200)
):
    rows = await chat_service.list_conversations(db, limit=limit)
    return ok([ChatConversationRead.model_validate(row).model_dump() for row in rows])


@router.post("/conversations")
async def create_conversation(
    db: AsyncSession = Depends(get_db),
    title: str = Form("新对话"),
    model_config_id: int | None = Form(None),
):
    conversation = await chat_service.create_conversation(
        db, title=title, model_config_id=model_config_id
    )
    return ok(ChatConversationRead.model_validate(conversation).model_dump())


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: int, db: AsyncSession = Depends(get_db)):
    """取会话详情 + 全部消息 + 附件（一次性返回，前端切换会话不用多次请求）。"""
    conversation = await chat_service.get_conversation(db, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    messages = await chat_service.list_messages(db, conversation_id)
    attachments = await chat_service.list_attachments(db, conversation_id)

    return ok(
        {
            "conversation": ChatConversationRead.model_validate(conversation).model_dump(),
            "messages": [to_message_read(row).model_dump() for row in messages],
            "attachments": [to_attachment_read(row).model_dump() for row in attachments],
        }
    )


@router.patch("/conversations/{conversation_id}")
async def update_conversation(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    title: str | None = Form(None),
    model_config_id: int | None = Form(None),
    mode: str | None = Form(None),
    reset_slots: bool = Form(False),
):
    """更新会话标题、所用模型或意图模式。

    ``mode`` 支持 ``auto``：清空模式让下一轮重新自动判断。
    换话题时配合 ``reset_slots=true`` 一起用 —— 否则槽位台账里会残留
    上一个话题的条件（比如换了城市还带着旧城市）。
    """
    conversation = await chat_service.get_conversation(db, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    if title is not None:
        conversation.title = title.strip()[:128] or conversation.title
    if model_config_id is not None:
        conversation.model_config_id = model_config_id
    if mode is not None:
        if mode == "auto":
            conversation.mode = UNSET_MODE
        elif mode in MODES:
            conversation.mode = mode
        else:
            raise HTTPException(
                status_code=422,
                detail=f"不支持的模式：{mode}（可选 {'、'.join(MODES)} 或 auto）",
            )
    if reset_slots:
        conversation.slot_state = json.dumps({}, ensure_ascii=False)

    await db.commit()
    await db.refresh(conversation)
    return ok(ChatConversationRead.model_validate(conversation).model_dump())


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: int, db: AsyncSession = Depends(get_db)):
    """删除会话，**并清理磁盘上的附件**（简历属于敏感文件，不能残留）。"""
    conversation = await chat_service.get_conversation(db, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    removed_files = ai.delete_conversation_files(conversation_id)
    await chat_service.delete_conversation(db, conversation)

    await log_event(
        db,
        f"删除对话会话 #{conversation_id}，清理附件 {removed_files} 个",
        category=LogCategory.SYSTEM,
    )
    return ok({"deleted": True, "removed_files": removed_files})


# ============================================================
# 附件
# ============================================================
@router.post("/conversations/{conversation_id}/attachments")
async def upload_attachment(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
):
    """上传附件：嗅探类型 → 校验大小 → 落盘 → 抽取文本。"""
    conversation = await chat_service.get_conversation(db, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    filename = file.filename or "unnamed"
    data = await file.read()

    if not data:
        raise HTTPException(status_code=422, detail="文件为空")

    detected = ai.sniff_file(data, filename)
    try:
        ai.validate_size(detected.kind, len(data))
    except ai.AttachmentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # 先建记录拿到 id，再按 id 命名落盘（避免用户文件名带来的路径穿越与重名）
    record = ChatAttachment(
        conversation_id=conversation_id,
        kind=detected.kind,
        filename=filename[:255],
        stored_path="",
        mime_type=detected.mime_type,
        size_bytes=len(data),
        extract_status="pending",
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    stored = ai.store_file(conversation_id, record.id, data, detected.suffix)
    record.stored_path = str(stored)

    # 抽取文本（图片标记为 not_applicable，交给视觉模型直接理解）
    text, status = ai.extract_text(stored, detected.kind)
    if status.startswith("failed:"):
        record.extract_status = "failed"
        record.extract_error = status[len("failed:") :]
    else:
        record.extract_status = status
        record.extracted_text = text or None

    await db.commit()
    await db.refresh(record)

    return ok(to_attachment_read(record, include_text=False).model_dump())


@router.get("/attachments/{attachment_id}")
async def get_attachment(attachment_id: int, db: AsyncSession = Depends(get_db)):
    """附件详情（含抽取文本，供排查解析质量）。"""
    row = await db.get(ChatAttachment, attachment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="附件不存在")
    return ok(to_attachment_read(row, include_text=True).model_dump())


@router.get("/attachments/{attachment_id}/raw")
async def get_attachment_raw(attachment_id: int, db: AsyncSession = Depends(get_db)):
    """返回附件原始内容（前端图片缩略图预览用）。"""
    from pathlib import Path

    from fastapi.responses import FileResponse

    row = await db.get(ChatAttachment, attachment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="附件不存在")

    path = Path(row.stored_path)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="附件文件已被清理")

    return FileResponse(
        path,
        media_type=row.mime_type or "application/octet-stream",
        filename=row.filename,
    )


# ============================================================
# 流式对话
# ============================================================
@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: int, payload: ChatSendRequest, db: AsyncSession = Depends(get_db)
):
    """发送消息并以 SSE 流式返回回复。

    注意：本接口**不做** HTTP 层的错误抛出（除 404/422 这类前置校验），
    生成过程中的失败一律以 ``error`` 事件下发，因为流一旦开始就无法改状态码。
    """
    conversation = await chat_service.get_conversation(db, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    text = (payload.content or "").strip()
    attachment_ids = payload.attachment_ids or []

    if not text and not attachment_ids:
        raise HTTPException(status_code=422, detail="消息内容与附件不能同时为空")

    if len(attachment_ids) > settings.chat_max_attachments:
        raise HTTPException(
            status_code=422,
            detail=f"单条消息最多 {settings.chat_max_attachments} 个附件",
        )

    # 会话级模型切换
    if payload.model_config_id is not None:
        conversation.model_config_id = payload.model_config_id
        await db.commit()
        await db.refresh(conversation)

    # 模式：显式指定则跳过自动路由；"auto" 表示重新自动判断
    forced_mode: str | None = None
    if payload.mode == "auto":
        conversation.mode = UNSET_MODE
        await db.commit()
        await db.refresh(conversation)
    elif payload.mode in MODES:
        forced_mode = payload.mode

    logger.info(
        "[API] 会话=%s 收到 mode 参数=%r -> forced_mode=%r 当前 mode=%r",
        conversation_id,
        payload.mode,
        forced_mode,
        conversation.mode,
    )

    await chat_service.persist_user_message(
        db, conversation, text=text, attachment_ids=attachment_ids
    )
    if text:
        await chat_service.auto_title(db, conversation, text)
    await db.refresh(conversation)

    generator = chat_service.stream_reply(
        db,
        conversation,
        user_text=text,
        attachment_ids=attachment_ids,
        forced_mode=forced_mode,
    )

    async def event_stream():
        """把 SSEFrame 逐个编码下发。

        必须是**异步**推导：``stream_reply`` 是异步生成器，
        同步推导式只会产出生成器对象本身，Starlette 迭代时会报
        ``async_generator object is not iterable``。
        """
        async for frame in generator:
            yield frame.encode()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=ai.SSE_HEADERS,
    )


@router.get("/config")
async def chat_config():
    """前端需要的对话配置（渲染上传限制的提示文案用）。"""
    return ok(
        {
            "max_image_bytes": settings.chat_max_image_bytes,
            "max_doc_bytes": settings.chat_max_doc_bytes,
            "max_attachments": settings.chat_max_attachments,
            "fake_llm": settings.fake_llm,
            "max_image_label": _human_size(settings.chat_max_image_bytes),
            "max_doc_label": _human_size(settings.chat_max_doc_bytes),
            "accepted": "JPEG / PNG / GIF / WebP / PDF / Word / 纯文本",
        }
    )
