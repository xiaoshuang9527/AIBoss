"""实测：通过工厂拿适配器并做一次真实对话补全。

这不是单元测试，是**真机验证** —— 用来确认 chat_completion 路径在真实推理
场景下可用（连接测试只发了一条 max_tokens=8 的极简消息，覆盖不到正文生成、
token 统计、工具声明等分支）。

注意：本脚本必须在 backend/ 目录下运行，因为 BZP_DATABASE_URL 里的
SQLite 路径是相对路径。
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

# 直接运行本脚本时，Python 只会把 tests/ 加进 sys.path，需要补上 backend/
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from bzp_workbench.db import SessionLocal
from bzp_workbench.services.llm import (
    Message,
    ToolDefinition,
    get_llm_client,
    resolve_model_config,
)


async def probe(model_config_id: int | None, *, with_tools: bool) -> None:
    async with SessionLocal() as db:
        config = await resolve_model_config(db, model_config_id)
        label = f"{config.display_name} ({config.provider}/{config.model_id})"
        print(f"\n{'=' * 72}\n{label}\n{'=' * 72}")

        adapter = await get_llm_client(db, config.id, require_enabled=False)

        tools = None
        if with_tools and "tool_calls" in config.capabilities:
            tools = [
                ToolDefinition(
                    name="search_existing_jobs",
                    description="从本地岗位库查询符合条件的岗位",
                    parameters={
                        "type": "object",
                        "properties": {
                            "keyword": {"type": "string", "description": "岗位关键词"},
                            "city": {"type": "string", "description": "城市"},
                        },
                        "required": ["keyword"],
                    },
                )
            ]

        messages = [
            Message.system(
                "你是一个求职辅助助手。回答要简短。"
                + ("如果需要查询岗位，请调用 search_existing_jobs 工具。" if tools else "")
            ),
            Message.user(
                "用一句话说明你在深圳找 Python 岗位时最该关注的三个筛选维度。"
                if not tools
                else "帮我在深圳找 Python 岗位。"
            ),
        ]

        started = time.monotonic()
        try:
            response = await adapter.chat_completion(messages, tools=tools, max_tokens=300)
        except Exception as exc:  # noqa: BLE001 - 真机验证，需要看到任何异常
            elapsed = (time.monotonic() - started) * 1000
            print(f"  ✗ 失败（{elapsed:.0f}ms）: {type(exc).__name__}")
            print(f"    {exc}")
            cause = getattr(exc, "detail", None)
            if cause:
                print(f"    detail: {cause}")
            return

        elapsed = (time.monotonic() - started) * 1000
        print(f"  ✓ 成功（{elapsed:.0f}ms）")
        print(f"  模型回显 : {response.model}")
        print(f"  结束原因 : {response.finish_reason}")
        print(
            f"  Token    : prompt={response.usage.prompt_tokens} "
            f"completion={response.usage.completion_tokens} "
            f"cached={response.usage.cached_tokens} "
            f"total={response.usage.total_tokens}"
        )
        if response.tool_calls:
            print(f"  工具调用 : {len(response.tool_calls)} 个")
            for call in response.tool_calls:
                print(f"    - {call.name}({call.arguments})")
        print(f"  正文     :")
        for line in (response.text or "(空)").strip().splitlines():
            print(f"    {line}")


async def probe_stream(model_config_id: int | None) -> None:
    """验证流式路径 —— 对话模块要用 SSE 逐字输出，这条路径必须通。"""
    from bzp_workbench.services.llm import StreamEventType

    async with SessionLocal() as db:
        config = await resolve_model_config(db, model_config_id)
        if config.model_id != "deepseek-flash":
            return
        print(f"\n{'=' * 72}\n流式验证 · {config.display_name}\n{'=' * 72}")
        adapter = await get_llm_client(db, config.id, require_enabled=False)
        messages = [Message.user("从 1 数到 5，只输出数字，用顿号分隔。")]

        started = time.monotonic()
        first_chunk_at: float | None = None
        text_parts: list[str] = []
        usage = None
        try:
            async for chunk in adapter.stream_chat(messages, max_tokens=64):
                if chunk.type is StreamEventType.TEXT:
                    if first_chunk_at is None:
                        first_chunk_at = time.monotonic()
                    text_parts.append(chunk.text)
                elif chunk.type is StreamEventType.USAGE:
                    usage = chunk.usage
                elif chunk.type is StreamEventType.DONE:
                    break
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ 流式失败: {type(exc).__name__}: {exc}")
            return

        total = (time.monotonic() - started) * 1000
        ttft = (first_chunk_at - started) * 1000 if first_chunk_at else -1
        print(f"  ✓ 首字延迟 {ttft:.0f}ms | 总耗时 {total:.0f}ms | 分片 {len(text_parts)} 个")
        print(f"  拼接结果 : {''.join(text_parts)!r}")
        if usage:
            print(f"  Token    : prompt={usage.prompt_tokens} completion={usage.completion_tokens}")


async def main() -> None:
    # 默认模型（DeepSeek）+ 带工具
    await probe(None, with_tools=True)
    # 第二个模型（Agnes）+ 不带工具
    await probe(2, with_tools=False)
    # 流式
    await probe_stream(None)


if __name__ == "__main__":
    asyncio.run(main())
