"""验证脚本的测试数据清理工具。

为什么需要它
------------
真机验证脚本会创建**真实的**对话与任务（这是验证的意义 —— 不用 mock）。
但如果不清理，它们会出现在用户的历史记录与对话列表里，看起来像是用户自己
产生的垃圾。之前就发生过：修完一个 bug 后，用户的对话列表里堆了十几个
「写入验证」「P3 验证-我」。

清理规则（宁可少删，不可错删）
------------------------------
* **任务**：只删已知的测试关键词（``pytest%`` 与脚本显式声明的）。
  绝不做模糊匹配 —— 用户的真实任务是宝贵数据。
* **对话**：三条规则，任一命中即认为是残留：

  1. ``mode == "unset"`` 且标题不是默认值 —— 真实对话一发出消息就会被
     路由成具体模式，所以 mode 还是 unset 说明是脚本直接建的
  2. 标题形如 ``pytest... @ <城市>`` —— 由 auto_title 从测试消息生成的
  3. **标题与该对话的任何一条消息都对不上** —— 这是最强的一条判据：
     真实对话的标题要么是用户自己写的，要么是 auto_title 取首条用户消息
     的开头，所以标题必然能在消息内容里找到。而脚本建的对话标题是
     「路由复现」「脏数据检查」这类说明性文字，与消息内容毫无关系。

  第 3 条不会误伤真实对话：只要标题能在自己的消息里找到出处，就保留。

用法::

    from manual_cleanup import cleanup_verification_data
    await cleanup_verification_data(extra_task_keywords=["Rust"])

放在脚本的 ``finally`` 里，确保异常路径也会清理。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

#: 测试任务的统一关键词前缀
PYTEST_PREFIX = "pytest"

#: 自动生成的标题形态：`关键词 @ 城市`
_AUTO_TITLE = re.compile(r"^.+?\s*@\s*\S+$")


async def cleanup_verification_data(
    *,
    extra_task_keywords: list[str] | None = None,
    extra_conversation_ids: list[int] | None = None,
) -> dict[str, int]:
    """清掉验证脚本留下的数据，返回各项删除数量。"""
    from sqlalchemy import delete, select

    from bzp_workbench.db import SessionLocal
    from bzp_workbench.models import (
        ChatAttachment,
        ChatConversation,
        ChatMessage,
        ScrapeTask,
    )

    summary = {"tasks": 0, "conversations": 0}
    keywords = list(extra_task_keywords or [])

    async with SessionLocal() as db:
        # ---- 1. 任务 ----
        result = await db.execute(
            delete(ScrapeTask).where(
                ScrapeTask.keyword.like(f"{PYTEST_PREFIX}%")
                | (ScrapeTask.keyword.in_(keywords) if keywords else False)
            )
        )
        summary["tasks"] = result.rowcount or 0

        # ---- 2. 对话 ----
        rows = (await db.execute(select(ChatConversation))).scalars().all()
        doomed: list[int] = []

        if extra_conversation_ids:
            doomed.extend(extra_conversation_ids)

        for conversation in rows:
            if conversation.id in doomed:
                continue
            title = (conversation.title or "").strip()
            # 规则 1：模式还没定 + 标题不是默认值 → 脚本建的
            if conversation.mode == "unset" and title and title != "新对话":
                doomed.append(conversation.id)
                continue
            # 规则 2：标题是自动生成的 `<关键词> @ <城市>`，且关键词是测试关键词
            if _AUTO_TITLE.match(title) and title.startswith(PYTEST_PREFIX):
                doomed.append(conversation.id)
                continue

        # 规则 3 需要消息内容，单独查一次
        for conversation in rows:
            if conversation.id in doomed:
                continue
            title = (conversation.title or "").strip()
            if not title or title == "新对话":
                continue  # 空对话不算残留（用户可能刚建了还没发消息）
            messages = (
                await db.execute(
                    select(ChatMessage.content).where(
                        ChatMessage.conversation_id == conversation.id,
                        ChatMessage.content.is_not(None),
                    )
                )
            ).scalars().all()
            if not messages:
                continue
            # 标题的前 12 个字应当能在某条消息里找到出处
            probe = title[:12]
            if not any(probe and probe in (content or "") for content in messages):
                doomed.append(conversation.id)

        if doomed:
            await db.execute(
                delete(ChatAttachment).where(ChatAttachment.conversation_id.in_(doomed))
            )
            await db.execute(
                delete(ChatMessage).where(ChatMessage.conversation_id.in_(doomed))
            )
            result = await db.execute(
                delete(ChatConversation).where(ChatConversation.id.in_(doomed))
            )
            summary["conversations"] = result.rowcount or 0

        await db.commit()

    return summary


async def _main() -> int:
    """直接运行本文件也能清理（用于事后补救）。"""
    summary = await cleanup_verification_data()
    print(f"已清理：任务 {summary['tasks']} 条，对话 {summary['conversations']} 个")
    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(_main()))
