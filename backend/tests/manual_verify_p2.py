"""真机验证 P2：让模型真的发起抓取任务。

会**真的调用 DeepSeek**，并**真的创建一个抓取任务**（随后立刻取消）。
运行前请确认：
* 后端在 127.0.0.1:18080 运行
* 授权档案状态为 active（否则工具会正确地拒绝执行）

用法（在 backend/ 目录下）：
    python tests/manual_verify_p2.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from bzp_workbench.config import settings

settings.fake_llm = False

from bzp_workbench.db import SessionLocal
from bzp_workbench.models import ChatConversation, ScrapeTask
from bzp_workbench.services.ai import service as chat_service

#: 故意选一个库里没有的领域，逼模型走"抓取"分支而不是"检索本地库"
CASES = [
    (
        "本地库没有数据 → 应发起抓取",
        "帮我抓一下深圳的 Rust 开发岗位，20-30K，抓 2 页就行",
    ),
    (
        "追问进度 → 应调用 check_task_status",
        "刚才那个任务现在什么进度了？",
    ),
    (
        "闲聊 → 不应调用任何岗位工具",
        "解释一下什么是所有权（ownership）",
    ),
]


async def drain(conversation_id: int, text: str, label: str) -> None:
    async with SessionLocal() as db:
        conversation = await db.get(ChatConversation, conversation_id)
        await chat_service.persist_user_message(
            db, conversation, text=text, attachment_ids=[]
        )
        await db.refresh(conversation)

        print(f"\n{'=' * 76}")
        print(f"【{label}】")
        print(f"用户：{text}")
        print(f"{'=' * 76}")

        counts: dict[str, int] = {}
        meta: dict = {}
        tool_calls: list[dict] = []
        tool_results: list[dict] = []
        text_parts: list[str] = []
        errors: list[dict] = []
        usage: dict = {}

        async for frame in chat_service.stream_reply(
            db, conversation, user_text=text, attachment_ids=[]
        ):
            name = frame.event.value
            counts[name] = counts.get(name, 0) + 1
            if name == "meta":
                meta = frame.data
            elif name == "text":
                text_parts.append(frame.data.get("delta", ""))
            elif name == "tool_call":
                tool_calls.append(frame.data)
            elif name == "tool_result":
                tool_results.append(frame.data)
            elif name == "error":
                errors.append(frame.data)
            elif name == "usage":
                usage = frame.data

        print(f"模式：{meta.get('mode')}（{meta.get('mode_reason')}）")
        print(f"事件：{counts}")
        print(
            f"Token：prompt={usage.get('prompt_tokens')} "
            f"completion={usage.get('completion_tokens')} "
            f"工具轮数={usage.get('tool_rounds')}"
        )

        for call in tool_calls:
            print(f"\n工具调用：{call.get('name')}")
            print(f"  参数：{call.get('arguments')}")
        for result in tool_results:
            data = result.get("data") or {}
            print(f"工具结果：{result.get('name')} -> {result.get('payload_type')}")
            if isinstance(data, dict) and "task_id" in data:
                print(f"  任务 #{data['task_id']}  城市={data.get('city')}  "
                      f"页数={data.get('max_pages')}  预计 {data.get('estimated_minutes')} 分钟")
                salary = data.get("salary") or {}
                if salary:
                    print(f"  薪资档位：{salary.get('code')} ({salary.get('label')})")
                    if salary.get("note"):
                        print(f"  档位说明：{salary['note'][:110]}…")
                print(f"  是否去重命中：{data.get('duplicate', False)}")
            elif isinstance(data, dict) and "tasks" in data:
                for item in data["tasks"][:3]:
                    print(f"  #{item['task_id']} {item['keyword']} @ {item['city']} "
                          f"-> {item['status_label']}")

        if errors:
            for error in errors:
                print(f"\n错误：{error.get('title')} — {error.get('message')}")

        answer = "".join(text_parts)
        print(f"\n回答（{len(answer)} 字）：")
        for line in answer.strip().splitlines()[:18]:
            print(f"  {line}")


async def main() -> int:
    created_task_ids: list[int] = []

    async with SessionLocal() as db:
        count = len((await db.execute(ScrapeTask.__table__.select())).all())
        print(f"当前库中任务数：{count}")
        conversation = ChatConversation(
            title="P2 真机验证", mode="job_hunting"
        )
        db.add(conversation)
        await db.commit()
        await db.refresh(conversation)
        conversation_id = conversation.id

    for label, text in CASES:
        await drain(conversation_id, text, label)

    # 收集本次新建的任务并取消，避免真在后台跑起来
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                ScrapeTask.__table__.select().order_by(ScrapeTask.__table__.c.id.desc()).limit(5)
            )
        ).all()
        for row in rows:
            if row.keyword in ("Rust 开发", "Rust"):
                created_task_ids.append(row.id)

    print(f"\n{'=' * 76}")
    if created_task_ids:
        print(f"⚠️  本次真的创建了任务：{created_task_ids}")
        print("    正在取消，避免它真的去抓（需要 Chrome 与登录态）…")
        from bzp_workbench.enums import TaskStatus

        async with SessionLocal() as db:
            for task_id in created_task_ids:
                task = await db.get(ScrapeTask, task_id)
                if task and task.status in ("pending", "running"):
                    task.status = TaskStatus.CANCELLED.value
                    task.stage_text = "真机验证后自动取消"
            await db.commit()
        print("    已取消。")
    else:
        print("本次没有创建抓取任务。")

    print(f"会话 id={conversation_id}")

    # 清掉本次造的任务与对话 —— 否则它们会出现在用户的历史记录里，
    # 看起来像用户自己产生的垃圾数据
    from manual_cleanup import cleanup_verification_data

    summary = await cleanup_verification_data(extra_conversation_ids=[conversation_id])
    print(f"已清理：任务 {summary['tasks']} 条，对话 {summary['conversations']} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
