"""对话用量统计（成本看板的数据层）。

设计原则
--------
* **不出现任何计费/额度字眼**。这是纯个人求职辅助工具，本模块只回答
  "我用了多少 token、花在哪"，**不做金额换算、不做预算告警**。
  用「用量统计」而不是「成本」作为对外名称。
* 全部统计基于 ``chat_messages`` 已记录的 token 字段，不额外引入埋点表 ——
  数据已经在那儿了（每条助手消息都记了 prompt/completion/cached/reasoning）。
* 时间口径统一用 **UTC 自然日**（与库里的存法一致），避免时区换算引入偏差。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import ChatConversation, ChatMessage
from ...utils import utcnow

#: 默认统计天数
DEFAULT_DAYS = 14


def _as_int(value: Any) -> int:
    return int(value or 0)


async def _totals(db: AsyncSession) -> dict[str, int]:
    """总量：会话数、消息数、各类 token。"""
    conversation_count = (
        await db.execute(select(func.count()).select_from(ChatConversation))
    ).scalar() or 0

    row = (
        await db.execute(
            select(
                func.count().label("messages"),
                func.coalesce(func.sum(ChatMessage.prompt_tokens), 0).label("prompt"),
                func.coalesce(func.sum(ChatMessage.completion_tokens), 0).label("completion"),
                func.coalesce(func.sum(ChatMessage.cached_tokens), 0).label("cached"),
                func.coalesce(func.sum(ChatMessage.reasoning_tokens), 0).label("reasoning"),
            )
            .select_from(ChatMessage)
            .where(ChatMessage.role == "assistant")
        )
    ).one()

    prompt = _as_int(row.prompt)
    completion = _as_int(row.completion)
    cached = _as_int(row.cached)
    reasoning = _as_int(row.reasoning)

    return {
        "conversations": _as_int(conversation_count),
        "assistant_messages": _as_int(row.messages),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_tokens": cached,
        "reasoning_tokens": reasoning,
        "total_tokens": prompt + completion,
        # 缓存命中率：命中部分单价更低，这个比例越高越省
        "cache_hit_rate": round(cached / prompt, 4) if prompt else 0.0,
        # 思考占输出的比例：推理模型里这一项经常很高，是"贵在哪"的直接答案
        "reasoning_share": round(reasoning / completion, 4) if completion else 0.0,
    }


async def _daily(db: AsyncSession, days: int) -> list[dict[str, Any]]:
    """按 UTC 自然日聚合。

    只返回**有数据的日期**，缺的日期由调用方补零 —— 在 SQL 里生成日期序列
    对 SQLite/PostgreSQL/MySQL 是三套写法，不值得。
    """
    since = (utcnow() - timedelta(days=max(1, days))).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    day = func.date(ChatMessage.created_at)
    rows = (
        await db.execute(
            select(
                day.label("day"),
                func.count().label("messages"),
                func.coalesce(func.sum(ChatMessage.prompt_tokens), 0).label("prompt"),
                func.coalesce(func.sum(ChatMessage.completion_tokens), 0).label("completion"),
                func.coalesce(func.sum(ChatMessage.reasoning_tokens), 0).label("reasoning"),
                func.coalesce(func.sum(ChatMessage.cached_tokens), 0).label("cached"),
            )
            .select_from(ChatMessage)
            .where(
                ChatMessage.role == "assistant",
                ChatMessage.created_at >= since,
            )
            .group_by(day)
            .order_by(day)
        )
    ).all()

    return [
        {
            "date": str(row.day),
            "messages": _as_int(row.messages),
            "prompt_tokens": _as_int(row.prompt),
            "completion_tokens": _as_int(row.completion),
            "reasoning_tokens": _as_int(row.reasoning),
            "cached_tokens": _as_int(row.cached),
            "total_tokens": _as_int(row.prompt) + _as_int(row.completion),
        }
        for row in rows
    ]


async def _by_model(db: AsyncSession) -> list[dict[str, Any]]:
    """按模型聚合。

    这是看板最有价值的一张表：能直接看出"哪个模型更贵/更慢"。
    实测两个配置好的模型延迟差 8 倍，用量差异同样明显。
    """
    rows = (
        await db.execute(
            select(
                ChatMessage.model_label,
                func.count().label("messages"),
                func.coalesce(func.sum(ChatMessage.prompt_tokens), 0).label("prompt"),
                func.coalesce(func.sum(ChatMessage.completion_tokens), 0).label("completion"),
                func.coalesce(func.sum(ChatMessage.reasoning_tokens), 0).label("reasoning"),
                func.coalesce(func.avg(ChatMessage.latency_ms), 0).label("avg_latency"),
            )
            .select_from(ChatMessage)
            .where(ChatMessage.role == "assistant")
            .group_by(ChatMessage.model_label)
            .order_by(func.coalesce(func.sum(ChatMessage.prompt_tokens), 0).desc())
        )
    ).all()

    return [
        {
            "model_label": row.model_label or "（未记录）",
            "messages": _as_int(row.messages),
            "prompt_tokens": _as_int(row.prompt),
            "completion_tokens": _as_int(row.completion),
            "reasoning_tokens": _as_int(row.reasoning),
            "total_tokens": _as_int(row.prompt) + _as_int(row.completion),
            "avg_latency_ms": int(_as_int(row.avg_latency)),
        }
        for row in rows
    ]


async def _top_conversations(db: AsyncSession, limit: int = 10) -> list[dict[str, Any]]:
    """用量最高的会话 —— 用来发现"哪个话题特别费 token"。"""
    rows = (
        await db.execute(
            select(
                ChatConversation.id,
                ChatConversation.title,
                ChatConversation.mode,
                func.count(ChatMessage.id).label("messages"),
                func.coalesce(func.sum(ChatMessage.prompt_tokens), 0).label("prompt"),
                func.coalesce(func.sum(ChatMessage.completion_tokens), 0).label("completion"),
            )
            .join(ChatMessage, ChatMessage.conversation_id == ChatConversation.id)
            .where(ChatMessage.role == "assistant")
            .group_by(ChatConversation.id)
            .order_by(
                (
                    func.coalesce(func.sum(ChatMessage.prompt_tokens), 0)
                    + func.coalesce(func.sum(ChatMessage.completion_tokens), 0)
                ).desc()
            )
            .limit(limit)
        )
    ).all()

    return [
        {
            "conversation_id": row.id,
            "title": row.title,
            "mode": row.mode,
            "messages": _as_int(row.messages),
            "prompt_tokens": _as_int(row.prompt),
            "completion_tokens": _as_int(row.completion),
            "total_tokens": _as_int(row.prompt) + _as_int(row.completion),
        }
        for row in rows
    ]


async def _tools_usage(db: AsyncSession, limit: int = 20) -> list[dict[str, Any]]:
    """工具调用次数统计。

    数据来源是 ``tool_calls_json``，用 LIKE 做粗匹配即可 —— 精确解析 JSON
    在各数据库上写法不一，而这里只需要"哪个工具用得最多"。
    """
    from ..ai.tools import tool_names

    rows = (
        await db.execute(
            select(ChatMessage.tool_calls_json).where(
                ChatMessage.role == "assistant",
                ChatMessage.tool_calls_json.is_not(None),
            )
        )
    ).scalars().all()

    counter: dict[str, int] = {}
    for raw in rows:
        text = raw or ""
        for name in tool_names():
            # 每个工具在 JSON 里出现一次算一次调用
            count = text.count(f'"{name}"')
            if count:
                counter[name] = counter.get(name, 0) + count

    return [
        {"name": name, "calls": calls}
        for name, calls in sorted(counter.items(), key=lambda item: -item[1])[:limit]
    ]


async def usage_report(db: AsyncSession, *, days: int = DEFAULT_DAYS) -> dict[str, Any]:
    """汇总一份用量报告。"""
    days = max(1, min(days, 180))
    return {
        "days": days,
        "generated_at": utcnow().isoformat(),
        "totals": await _totals(db),
        "daily": await _daily(db, days),
        "by_model": await _by_model(db),
        "top_conversations": await _top_conversations(db),
        "tools": await _tools_usage(db),
    }


__all__ = ["DEFAULT_DAYS", "usage_report"]
