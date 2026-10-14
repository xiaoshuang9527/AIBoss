"""对话编排：把数据库、适配器、路由、工具循环、SSE 串起来。

一次「发消息」的完整流程
------------------------
1. 落库用户消息（含附件引用）
2. 选取模型（会话指定 → 全局默认）并构造适配器
3. **惰性意图路由**：首轮或未定模式时才调模型判断，之后沿用（省一次往返）
4. 组装上下文：历史消息 + 按模式选 System Prompt + 附件文本/图片
5. 交给 :func:`runner.run_turn` 跑「模型 → 工具 → 模型」循环
6. 落库助手消息（正文 / 思考过程 / 工具调用 / token / 耗时）

分层约定：
* :mod:`router` 只管「这是什么意图」
* :mod:`runner` 只管「循环与内容事件」
* 本模块只管「编排与落库」
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...enums import LogCategory
from ...models import ChatAttachment, ChatConversation, ChatMessage
from ...utils import utcnow
from ..llm import (
    LLMError,
    LLMErrorType,
    Message,
    Role,
    get_llm_client,
    normalize_capabilities,
    resolve_model_config,
    wrap_exception,
)
from ..logging_service import log_event, logger
from .attachments import build_attachment_context, to_image_part
from .fake_driver import FakeLLMDriver
from .prompts import BASE_SYSTEM_PROMPT, INTERVIEW_COACH_PROMPT, JOB_HUNTING_PROMPT
from .router import MODES, UNSET_MODE, RouteDecision, route
from .runner import TurnResult, run_turn
from .stream import SSEEvent, SSEFrame
from .tools.base import ToolContext
from .stream import SSEEvent, SSEFrame

#: 送入模型的最大历史消息条数（防止上下文无限增长）
MAX_HISTORY_MESSAGES = 40

#: 各意图模式对应的 System Prompt。
#: 路由定下模式后，这里决定给模型看哪套指令。
MODE_SYSTEM_PROMPTS: dict[str, str] = {
    "job_hunting": f"{BASE_SYSTEM_PROMPT}\n\n{'=' * 60}\n\n{JOB_HUNTING_PROMPT}",
    "interview_coach": f"{BASE_SYSTEM_PROMPT}\n\n{'=' * 60}\n\n{INTERVIEW_COACH_PROMPT}",
    "chat": BASE_SYSTEM_PROMPT,
}

#: 会话初始模式。
#:
#: ⚠️ 必须是 ``unset`` 而**不能是** ``chat``：路由用「模式是否已定」来决定要不要
#: 调模型判断意图，如果初始值就是 ``chat``，首轮会被判成"模式已定"从而跳过路由，
#: 用户第一句「帮我找深圳的 Python 岗位」会被当成闲聊处理。
DEFAULT_MODE = UNSET_MODE

#: 会被持久化成消息结构化载荷的工具结果类型。
#: 前端按这些类型分发到对应卡片组件（见 MessageBubble.vue 的 payloads）。
STRUCTURED_PAYLOAD_TYPES = (
    "job_cards",     # 岗位列表
    "job_detail",    # 单个岗位的完整 JD
    "task_created",  # 抓取任务已创建
    "task_status",   # 抓取任务进度
    "match_report",  # 简历 × 岗位匹配度诊断报告（P4）
    "resume",        # 简历解析结果
)


def system_prompt_for(mode: str) -> str:
    return MODE_SYSTEM_PROMPTS.get(mode, MODE_SYSTEM_PROMPTS["chat"])


@dataclass
class StreamOutcome:
    """流式结束后汇总的结果，用于落库。"""

    text: str = ""
    reasoning: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    finish_reason: str = "stop"
    latency_ms: int = 0
    error_type: str | None = None
    error_message: str | None = None
    model_label: str = ""


# ============================================================
# 会话
# ============================================================
async def create_conversation(
    db: AsyncSession, *, title: str = "新对话", model_config_id: int | None = None
) -> ChatConversation:
    conversation = ChatConversation(
        title=title[:128],
        mode=DEFAULT_MODE,
        model_config_id=model_config_id,
        slot_state=json.dumps({}, ensure_ascii=False),
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


async def get_conversation(db: AsyncSession, conversation_id: int) -> ChatConversation | None:
    return await db.get(ChatConversation, conversation_id)


async def list_conversations(db: AsyncSession, limit: int = 50) -> list[ChatConversation]:
    statement = (
        select(ChatConversation)
        .order_by(ChatConversation.updated_at.desc(), ChatConversation.id.desc())
        .limit(limit)
    )
    return list((await db.execute(statement)).scalars().all())


async def list_messages(
    db: AsyncSession, conversation_id: int, limit: int = 200
) -> list[ChatMessage]:
    statement = (
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
        .limit(limit)
    )
    return list((await db.execute(statement)).scalars().all())


async def list_attachments(
    db: AsyncSession, conversation_id: int
) -> list[ChatAttachment]:
    statement = (
        select(ChatAttachment)
        .where(ChatAttachment.conversation_id == conversation_id)
        .order_by(ChatAttachment.id.asc())
    )
    return list((await db.execute(statement)).scalars().all())


async def delete_conversation(db: AsyncSession, conversation: ChatConversation) -> None:
    """删除会话。附件文件由调用方一并清理（涉及磁盘 IO，放在 API 层）。"""
    await db.delete(conversation)
    await db.commit()


async def auto_title(db: AsyncSession, conversation: ChatConversation, first_text: str) -> None:
    """用首条用户消息生成会话标题，避免列表里全是「新对话」。"""
    if conversation.title and conversation.title != "新对话":
        return
    cleaned = " ".join(first_text.split())[:30]
    if cleaned:
        conversation.title = cleaned
        await db.commit()


# ============================================================
# 发消息（流式）
# ============================================================
async def persist_user_message(
    db: AsyncSession,
    conversation: ChatConversation,
    *,
    text: str,
    attachment_ids: list[int],
) -> ChatMessage:
    """落库用户消息并绑定附件。"""
    message = ChatMessage(
        conversation_id=conversation.id,
        role="user",
        content=text,
        content_type="text",
        attachments_json=json.dumps(attachment_ids, ensure_ascii=False) if attachment_ids else None,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)

    if attachment_ids:
        rows = (
            await db.execute(
                select(ChatAttachment).where(ChatAttachment.id.in_(attachment_ids))
            )
        ).scalars().all()
        for row in rows:
            row.message_id = message.id
            row.conversation_id = conversation.id
        await db.commit()

    conversation.message_count = (conversation.message_count or 0) + 1
    conversation.last_message_at = utcnow()
    await db.commit()
    await db.refresh(message)
    return message


def _history_to_messages(history: list[ChatMessage]) -> list[Message]:
    """把数据库历史转成适配器消息，并做上下文裁剪。"""
    trimmed = history[-MAX_HISTORY_MESSAGES:]
    messages: list[Message] = []
    for item in trimmed:
        if item.role == "user":
            messages.append(Message.user(item.content or ""))
        elif item.role == "assistant":
            if item.content:
                messages.append(Message.assistant(item.content))
        # system / tool 角色在 P0 不参与重建（工具循环 P2 才引入）
    return messages


async def _build_context(
    db: AsyncSession,
    conversation: ChatConversation,
    user_text: str,
    attachment_ids: list[int],
    *,
    mode: str = DEFAULT_MODE,
) -> tuple[list[Message], list[Any]]:
    """组装发给模型的完整消息序列。

    System Prompt 由 ``mode`` 决定 —— 这是模式一/模式二行为差异的根源。
    历史消息统一放在 system 之后、本轮 user 之前。
    """
    history = await list_messages(db, conversation.id)
    # 最新一条是刚写入的用户消息，先排除再统一附加，避免重复
    if history and history[-1].role == "user":
        history = history[:-1]

    messages: list[Message] = [Message.system(system_prompt_for(mode))]
    messages.extend(_history_to_messages(history))

    attachments: list[ChatAttachment] = []
    if attachment_ids:
        rows = (
            await db.execute(
                select(ChatAttachment).where(ChatAttachment.id.in_(attachment_ids))
            )
        ).scalars().all()
        attachments = list(rows)

    # 附件文本注入（图片走多模态，文档走文本）
    attachment_context = build_attachment_context(attachments)
    composed_text = user_text
    if attachment_context:
        composed_text = f"{user_text}\n\n{attachment_context}" if user_text else attachment_context

    images = []
    for item in attachments:
        if item.kind != "image":
            continue
        try:
            from pathlib import Path

            images.append(to_image_part(Path(item.stored_path), item.mime_type or "image/png"))
        except Exception as exc:  # noqa: BLE001
            # 单张图片读取失败不应让整条消息发不出去 —— 文本上下文仍然有效，
            # 但必须记日志，否则用户只会看到"回复变差了"而查不到原因。
            logger.warning("读取图片附件失败（已跳过）id=%s: %s", item.id, exc)
            continue

    messages.append(Message.user(composed_text, images))
    return messages, attachments


async def stream_reply(
    db: AsyncSession,
    conversation: ChatConversation,
    *,
    user_text: str,
    attachment_ids: list[int],
    forced_mode: str | None = None,
) -> AsyncIterator[SSEFrame]:
    """流式生成回复，逐帧 yield SSE。

    流程：
    1. 选定模型（会话指定 → 全局默认）
    2. 意图路由（惰性：首轮/未定模式才调模型；``forced_mode`` 直接指定）
    3. 组装上下文（历史 + 按模式选 System Prompt + 附件）
    4. 交给 :func:`runner.run_turn` 跑「模型 → 工具 → 模型」循环
    5. 落库助手消息（正文 / 思考 / 工具调用 / token）

    ``forced_mode``：用户手动指定的模式，传了就跳过路由。
    这是对惰性路由的必要补充 —— 惰性路由在话题跳转时不会重新判断
    （那是为了省一次模型往返的刻意取舍），所以必须给用户一个纠正入口。

    注意这里是**异步生成器**（调用方直接 ``async for``，不要 ``await``）——
    响应头必须先发出去再开始生成，否则首字延迟会等于整段生成时间。

    设计约束：``StreamingResponse`` 在响应开始后无法再改 HTTP 状态码，
    因此**所有错误都必须转成 ``error`` 事件**，不能抛异常。
    """
    started = time.monotonic()

    # ---- 1) 选定模型 ----
    try:
        config = await resolve_model_config(db, conversation.model_config_id)
    except LLMError as exc:
        yield SSEFrame(SSEEvent.ERROR, exc.to_payload())
        yield SSEFrame(SSEEvent.DONE, {"finish_reason": "error"})
        return
    except Exception as exc:  # noqa: BLE001 - 数据库等意外错误也要变成事件
        wrapped = wrap_exception(exc, context="解析模型配置")
        yield SSEFrame(SSEEvent.ERROR, wrapped.to_payload())
        yield SSEFrame(SSEEvent.DONE, {"finish_reason": "error"})
        return

    # ---- 2) 取驱动 ----
    if settings.fake_llm:
        driver: Any = FakeLLMDriver()
    else:
        try:
            driver = await get_llm_client(db, config.id, require_enabled=True)
        except LLMError as exc:
            yield SSEFrame(SSEEvent.ERROR, exc.to_payload())
            yield SSEFrame(SSEEvent.DONE, {"finish_reason": "error"})
            return

    # 路由用的适配器：假模型模式下用假驱动，保持行为一致
    router_adapter = FakeLLMDriver() if settings.fake_llm else driver

    # ---- 3) 意图路由 ----
    if forced_mode in MODES:
        # 用户手动指定：跳过路由，省一次模型往返
        decision = RouteDecision(
            mode=forced_mode, reason="用户手动指定", confidence=1.0, explicit=True
        )
    else:
        existing = await list_messages(db, conversation.id)
        # 刚写入的用户消息在最后一条，历史里除了它还剩几条就说明是不是首轮
        is_first_turn = len([m for m in existing if m.role in ("user", "assistant")]) <= 1

        logger.info(
            "[路由] 会话=%s 当前模式=%r 历史消息=%s 判定首轮=%s",
            conversation.id,
            conversation.mode,
            len(existing),
            is_first_turn,
        )

        decision = await route(
            router_adapter,
            user_text,
            current_mode=conversation.mode,
            is_first_turn=is_first_turn,
        )
        logger.info(
            "[路由] 结果 mode=%s reason=%r used_model=%s",
            decision.mode,
            decision.reason,
            decision.used_model,
        )
    mode = decision.mode

    # ⚠️ 必须重新从本 session 取一次会话对象，不能直接用传进来的引用。
    #
    # 实测踩过的坑：路由判断出 job_hunting 后赋值给 conversation.mode 并 commit，
    # 但 SQL 日志里**没有产生 UPDATE** —— 因为传入的对象已经不在本 session 上
    # （detached），SQLAlchemy 不会为 detached 对象生成 flush，赋值被静默丢弃。
    # 后果很隐蔽：模式每轮都要重新路由（多一次模型调用、成本翻倍），
    # 而且界面上模式标签一直停在旧值。
    #
    # 重新 get 一次拿到的对象一定处于本 session 的持久态，从此所有属性修改都能落库。
    bound = await db.get(ChatConversation, conversation.id)
    if bound is None:
        yield SSEFrame(
            SSEEvent.ERROR,
            {
                "error_type": LLMErrorType.CONFIG_ERROR.value,
                "title": "会话不存在",
                "message": "该会话可能已被删除。",
                "suggestion": "请新建一个会话后重试。",
            },
        )
        yield SSEFrame(SSEEvent.DONE, {"finish_reason": "error"})
        return
    conversation = bound

    if mode != conversation.mode:
        previous = conversation.mode
        conversation.mode = mode
        await db.commit()
        # 提交后立刻回读复核，确保真的落库了（见上面注释）
        fresh = await db.get(ChatConversation, conversation.id)
        logger.info(
            "[模式] 会话=%s %r -> %r（DB 复核=%r）",
            conversation.id,
            previous,
            mode,
            fresh.mode if fresh else None,
        )
        await log_event(
            db,
            f"会话 #{conversation.id} 模式切换为 {mode}（{decision.reason}）",
            category=LogCategory.SYSTEM,
        )

    model_label = config.display_name
    if settings.fake_llm:
        model_label = f"{config.display_name}（假模型 echo）"

    yield SSEFrame(
        SSEEvent.META,
        {
            "conversation_id": conversation.id,
            "model_config_id": config.id,
            "model_label": model_label,
            "mode": mode,
            "mode_reason": decision.reason,
            "mode_confidence": decision.confidence,
            "fake": settings.fake_llm,
        },
    )

    # ---- 4) 组装上下文 ----
    try:
        messages, attachments = await _build_context(
            db, conversation, user_text, attachment_ids, mode=mode
        )
    except Exception as exc:  # noqa: BLE001
        yield SSEFrame(
            SSEEvent.ERROR,
            {
                "error_type": LLMErrorType.UNKNOWN.value,
                "title": "上下文组装失败",
                "message": f"{type(exc).__name__}: {exc}",
                "suggestion": "请重试；若持续失败请查看后端日志。",
            },
        )
        yield SSEFrame(SSEEvent.DONE, {"finish_reason": "error"})
        return

    # ---- 5) 工具调用循环 ----
    capabilities = normalize_capabilities(config.capabilities)
    tool_context = ToolContext(
        db=db,
        conversation_id=conversation.id,
        model_config_id=config.id,
        slot_state=_loads(conversation.slot_state, {}),
        # 需要二次调模型的工具（analyze_match）用它
        adapter=driver,
    )

    turn = TurnResult()
    async for frame in run_turn(
        driver,
        messages=messages,
        mode=mode,
        result=turn,
        tool_context=tool_context,
        model_label=model_label,
        model_capabilities=capabilities,
        max_tokens=settings.chat_max_tokens,
    ):
        yield frame

    turn.latency_ms = turn.latency_ms or int((time.monotonic() - started) * 1000)

    # ---- 6) 落库 ----
    outcome = StreamOutcome(
        text=turn.text,
        reasoning=turn.reasoning,
        tool_calls=turn.tool_calls,
        tool_results=turn.tool_results,
        prompt_tokens=turn.prompt_tokens,
        completion_tokens=turn.completion_tokens,
        cached_tokens=turn.cached_tokens,
        finish_reason=turn.finish_reason,
        latency_ms=turn.latency_ms,
        error_type=turn.error_type,
        error_message=turn.error_message,
        model_label=model_label,
    )
    try:
        await _persist_assistant_message(db, conversation, config, outcome)
    except Exception as exc:  # noqa: BLE001 - 落库失败不应中断已建立的流
        logger.exception("保存助手消息失败：%s", exc)

    yield SSEFrame(
        SSEEvent.USAGE,
        {
            "prompt_tokens": outcome.prompt_tokens,
            "completion_tokens": outcome.completion_tokens,
            "cached_tokens": outcome.cached_tokens,
            "latency_ms": outcome.latency_ms,
            "tool_rounds": len(outcome.tool_results),
        },
    )
    yield SSEFrame(SSEEvent.DONE, {"finish_reason": outcome.finish_reason})


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


async def _persist_assistant_message(
    db: AsyncSession,
    conversation: ChatConversation,
    config: Any,
    outcome: StreamOutcome,
) -> None:
    """把助手回复落库。

    工具结果（如岗位卡片）以结构化载荷存进 ``content_json``，
    前端据此渲染卡片而不是纯文本 —— 这样刷新页面后卡片仍然可点击。
    """
    content_type = "text"
    content_data: dict[str, Any] | None = None

    # 取最后一个有结构化数据的工具结果作为消息载荷。
    # 前端据此渲染卡片（岗位列表 / 抓取任务 / 任务进度）而不是纯文本 ——
    # 这样刷新页面后卡片仍然可点击、进度仍然是结构化的。
    structured = [
        item
        for item in outcome.tool_results
        if item.get("payload_type") in STRUCTURED_PAYLOAD_TYPES
        and item.get("data") is not None
    ]
    if structured:
        last = structured[-1]
        content_type = str(last["payload_type"])
        content_data = {"payload_type": content_type, "data": last.get("data")}

    message = ChatMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=outcome.text or None,
        content_type=content_type,
        content_json=(
            json.dumps(content_data, ensure_ascii=False) if content_data else None
        ),
        reasoning=outcome.reasoning or None,
        tool_calls_json=(
            json.dumps(outcome.tool_calls, ensure_ascii=False) if outcome.tool_calls else None
        ),
        prompt_tokens=outcome.prompt_tokens,
        completion_tokens=outcome.completion_tokens,
        cached_tokens=outcome.cached_tokens,
        latency_ms=outcome.latency_ms,
        error_type=outcome.error_type,
        error_message=outcome.error_message,
        model_config_id=config.id,
        model_label=outcome.model_label,
    )
    db.add(message)

    conversation.message_count = (conversation.message_count or 0) + 1
    conversation.total_tokens = (conversation.total_tokens or 0) + (
        outcome.prompt_tokens + outcome.completion_tokens
    )
    conversation.last_message_at = utcnow()
    await db.commit()


__all__ = [
    "MAX_HISTORY_MESSAGES",
    "StreamOutcome",
    "auto_title",
    "create_conversation",
    "delete_conversation",
    "get_conversation",
    "list_attachments",
    "list_conversations",
    "list_messages",
    "persist_user_message",
    "stream_reply",
]
