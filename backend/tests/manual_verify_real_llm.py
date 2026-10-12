"""真机验证：真实模型 + 工具调用循环 + 意图路由。

这个脚本会**真的调用 DeepSeek API**，因此会消耗少量 token。
它的价值在于验证单测覆盖不到的东西：模型是否会正确调用工具、
工具结果回填后能否组织成人话、路由判断是否准确。

用法（必须在 backend/ 目录下运行）：
    python tests/manual_verify_real_llm.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from bzp_workbench.db import SessionLocal
from bzp_workbench.models import ChatConversation
from bzp_workbench.services.ai import service as chat_service
from bzp_workbench.services.ai.router import route
from bzp_workbench.services.llm import get_llm_client, resolve_model_config

# 关键：本次要验证真实链路，关掉假模型
from bzp_workbench.config import settings

settings.fake_llm = False

CASES = [
    ("首轮 · 找岗位（应路由到 job_hunting 并调用工具）", "帮我在深圳找 Python 后端岗位"),
    ("第二轮 · 追问（应沿用模式，不再路由）", "那薪资大概什么水平？"),
    ("闲聊（应路由到 chat，不调工具）", "解释一下什么是 REST 接口"),
]


async def drain(conversation_id: int, text: str, label: str) -> None:
    """发一条消息并打印事件流摘要。"""
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

        started = time.monotonic()
        counts: dict[str, int] = {}
        first_text_at: float | None = None
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[dict] = []
        tool_results: list[dict] = []
        meta: dict = {}
        errors: list[dict] = []
        usage: dict = {}

        async for frame in chat_service.stream_reply(
            db, conversation, user_text=text, attachment_ids=[]
        ):
            name = frame.event.value
            counts[name] = counts.get(name, 0) + 1
            if name == "meta":
                meta = frame.data
            elif name == "reasoning":
                reasoning_parts.append(frame.data.get("delta", ""))
            elif name == "text":
                if first_text_at is None:
                    first_text_at = time.monotonic()
                text_parts.append(frame.data.get("delta", ""))
            elif name == "tool_call":
                tool_calls.append(frame.data)
            elif name == "tool_result":
                tool_results.append(frame.data)
            elif name == "error":
                errors.append(frame.data)
            elif name == "usage":
                usage = frame.data

        total_ms = (time.monotonic() - started) * 1000
        ttft = (first_text_at - started) * 1000 if first_text_at else -1

        print(f"\n路由  ：mode={meta.get('mode')}  理由={meta.get('mode_reason')}  "
              f"置信度={meta.get('mode_confidence')}")
        print(f"模型  ：{meta.get('model_label')}")
        print(f"事件  ：{counts}")
        print(f"首字  ：{ttft:.0f}ms | 总耗时 {total_ms:.0f}ms")
        print(f"Token ：prompt={usage.get('prompt_tokens')} "
              f"completion={usage.get('completion_tokens')} "
              f"cached={usage.get('cached_tokens')} "
              f"工具轮数={usage.get('tool_rounds')}")

        if reasoning_parts:
            joined = "".join(reasoning_parts)
            print(f"\n思考（{len(joined)} 字，前 200 字）：\n  {joined[:200].strip()}")

        for call in tool_calls:
            print(f"\n工具调用：{call.get('name')}  参数={call.get('arguments')}")
        for result in tool_results:
            data = result.get("data") or {}
            jobs = data.get("jobs") if isinstance(data, dict) else None
            print(f"工具结果：{result.get('name')}  payload_type={result.get('payload_type')} "
                  f"命中={data.get('total') if isinstance(data, dict) else '?'} "
                  f"返回={len(jobs) if jobs else 0} 条")
            if jobs:
                for job in jobs[:3]:
                    print(f"    · {job.get('title')} | {job.get('company')} | {job.get('salary')}")
                    print(f"      链接 {job.get('job_link')}")

        if errors:
            for error in errors:
                print(f"\n错误：{error.get('title')} — {error.get('message')}")
                print(f"      建议：{error.get('suggestion')}")

        answer = "".join(text_parts)
        print(f"\n回答（{len(answer)} 字）：")
        for line in answer.strip().splitlines()[:16]:
            print(f"  {line}")


async def main() -> int:
    async with SessionLocal() as db:
        config = await resolve_model_config(db, None)
        print(f"使用模型：{config.display_name} ({config.provider}/{config.model_id})")
        print(f"能力标签：{config.capabilities}")

        adapter = await get_llm_client(db, config.id, require_enabled=False)

        # 先单独验证一次路由判断的准确性
        print(f"\n{'-' * 76}\n路由判断抽样\n{'-' * 76}")
        for text in ("帮我在深圳找 Python 岗位", "解释一下什么是分布式事务", "我想针对这个岗位准备面试"):
            decision = await route(adapter, text, is_first_turn=True)
            print(f"  {text:24} -> {decision.mode:16} ({decision.reason[:30]})")

        conversation = ChatConversation(title="P1 真机验证", mode="chat", model_config_id=config.id)
        db.add(conversation)
        await db.commit()
        await db.refresh(conversation)
        conversation_id = conversation.id

    for label, text in CASES:
        await drain(conversation_id, text, label)

    print(f"\n{'=' * 76}")
    print(f"验证完成。会话 id={conversation_id}（可在界面里查看，或手动删除）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
