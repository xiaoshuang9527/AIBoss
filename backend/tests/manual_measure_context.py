"""上下文体积量化：一次真实请求的 prompt 由什么组成。

用途：决定 P6 该优化什么。跑在真实数据上，而不是凭直觉猜。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bzp_workbench.db import SessionLocal
from bzp_workbench.models import ChatMessage
from bzp_workbench.services.ai.service import (
    MAX_HISTORY_MESSAGES,
    _history_to_messages,
    system_prompt_for,
)
from bzp_workbench.services.ai.tools import tools_for_mode

#: 粗略估算：中文约 1.5 字/token，英文约 4 字符/token。
#: 混合内容取 1.6 字/token —— 不求精确，只要能看出量级与占比。
CHARS_PER_TOKEN = 1.6


def est(text: str | None) -> int:
    return int(len(text or "") / CHARS_PER_TOKEN)


def main() -> int:
    async def run() -> None:
        async with SessionLocal() as db:
            # 找一条消息最多的会话
            from sqlalchemy import func, select

            row = (
                await db.execute(
                    select(
                        ChatMessage.conversation_id,
                        func.count().label("n"),
                    )
                    .group_by(ChatMessage.conversation_id)
                    .order_by(func.count().desc())
                    .limit(1)
                )
            ).first()
            if row is None:
                print("没有对话数据")
                return
            conversation_id = row[0]
            messages = (
                await db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.conversation_id == conversation_id)
                    .order_by(ChatMessage.id)
                )
            ).scalars().all()

        print(f"样本：对话 #{conversation_id}，共 {len(messages)} 条消息\n")

        for mode in ("job_hunting", "interview_coach", "chat"):
            system = system_prompt_for(mode)
            tools = tools_for_mode(mode)
            tool_chars = sum(
                len(str(t.parameters)) + len(t.description) for t in tools
            )

            history = _history_to_messages(messages)
            # 历史里每条消息正文的估算
            history_tokens = sum(est(m.content or "") for m in history)

            # 结构化载荷（content_json）现在被丢弃了多少
            dropped = sum(len(m.content_json or "") for m in messages)

            print(f"【{mode}】")
            print(f"  System Prompt      : {len(system):6} 字  ≈ {est(system):5} token")
            print(f"  工具定义（{len(tools)} 个）   : {tool_chars:6} 字  ≈ {est(str(tool_chars)):5} 字符")
            print(f"  历史消息（重建后）  : {history_tokens:5} token（{len(history)} 条）")
            print(f"  被丢弃的 content_json: {dropped:6} 字  ← 岗位卡片等结构化结果")
            total = est(system) + int(tool_chars / CHARS_PER_TOKEN) + history_tokens
            print(f"  ── 保守合计       : ≈ {total:5} token")
            print()

        print("说明：以上不含 tool 消息（工具返回的文本），那部分只在本轮有效。")
        print(f"当前按条数裁剪：最近 {MAX_HISTORY_MESSAGES} 条。")

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
