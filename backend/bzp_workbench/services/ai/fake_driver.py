"""假模型 echo 驱动（P0 骨架用）。

为什么需要它
------------
P0 阶段的目标是验证「上传 → 发送 → SSE 流式 → 前端渲染」整条链路，
而不是验证模型。用假模型有两个好处：

1. **不消耗 API 额度**就能反复调试前端交互；
2. 能**确定性地**产生各类事件（含 reasoning、tool_call、error），
   而真实模型的输出不可控，出问题时难判断是链路坏了还是模型行为不同。

它会刻意模拟真实模型的输出特征：
* 先流一段 ``reasoning``（推理模型的思考过程），**再**流 ``text``；
* 命中岗位类关键词时发起 ``tool_call``，走完工具事件的完整路径。

开关：``BZP_FAKE_LLM=true``（本地开发默认开启，生产必须关闭）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from ..llm import (
    LLMResponse,
    Message,
    StreamChunk,
    StreamEventType,
    TokenUsage,
    ToolCall,
)


def _looks_like_job_query(text: str) -> tuple[bool, str, str]:
    """粗略识别「找岗位」意图，返回 (命中, 关键词, 城市)。

    只用于假模型造数据，不承担真实的意图识别职责 —— 那是 System Prompt 的事。
    """
    cities = ["深圳", "北京", "上海", "广州", "杭州", "成都", "武汉", "南京", "西安", "苏州"]
    keywords = ["Python", "前端", "Java", "产品经理", "算法", "测试", "运维", "Go", "AI", "数据"]

    hit_city = next((city for city in cities if city in text), "")
    hit_keyword = next((kw for kw in keywords if kw.lower() in text.lower()), "")

    if hit_city or hit_keyword:
        return True, hit_keyword or "Python", hit_city or "深圳"
    return False, "", ""


class FakeLLMDriver:
    """假驱动：接口与真实适配器一致，但不访问网络。"""

    #: 每片之间的间隔，模拟逐字输出
    text_delay: float = 0.02
    reasoning_delay: float = 0.012

    def __init__(self, model_label: str = "假模型（echo）") -> None:
        self.model_label = model_label

    # ------------------------------------------------------------------
    async def chat_completion(
        self, messages: list[Message], **_: object
    ) -> LLMResponse:
        """非流式：直接拼一段回复。"""
        reply, reasoning, tool_calls = self._compose(messages)
        return LLMResponse(
            text=reply,
            reasoning=reasoning,
            tool_calls=tool_calls,
            finish_reason="tool_calls" if tool_calls else "stop",
            usage=TokenUsage(
                prompt_tokens=self._estimate(messages),
                completion_tokens=len(reply),
                cached_tokens=0,
            ),
            model="fake-echo",
        )

    async def stream_chat(
        self, messages: list[Message], **_: object
    ) -> AsyncIterator[StreamChunk]:
        """流式：先思考、再正文、必要时发起工具调用。"""
        reply, reasoning, tool_calls = self._compose(messages)

        # ---- 第一阶段：思考过程 ----
        if reasoning:
            for piece in self._chunks(reasoning, 6):
                await asyncio.sleep(self.reasoning_delay)
                yield StreamChunk(type=StreamEventType.REASONING, text=piece)

        # ---- 第二阶段：正文 ----
        for piece in self._chunks(reply, 3):
            await asyncio.sleep(self.text_delay)
            yield StreamChunk(type=StreamEventType.TEXT, text=piece)

        # ---- 第三阶段：工具调用 ----
        for call in tool_calls:
            yield StreamChunk(type=StreamEventType.TOOL_CALL, tool_call=call)

        yield StreamChunk(
            type=StreamEventType.USAGE,
            usage=TokenUsage(
                prompt_tokens=self._estimate(messages),
                completion_tokens=len(reply),
            ),
        )
        yield StreamChunk(
            type=StreamEventType.DONE,
            finish_reason="tool_calls" if tool_calls else "stop",
        )

    # ------------------------------------------------------------------
    def _compose(
        self, messages: list[Message]
    ) -> tuple[str, str, list[ToolCall]]:
        """由最后一条用户消息生成 (正文, 思考, 工具调用)。"""
        user_text = ""
        for message in reversed(messages):
            if message.role.value == "user" and message.content:
                user_text = message.content
                break

        attachment_count = sum(len(m.images) for m in messages)
        is_job, keyword, city = _looks_like_job_query(user_text)

        reasoning_lines = [
            f"用户说：「{user_text[:60] or '(无文本)'}」。",
        ]
        if attachment_count:
            reasoning_lines.append(f"随消息带了 {attachment_count} 张图片，需要看图理解。")
        if is_job:
            reasoning_lines.append(
                f"判断这是找岗位的请求，关键词={keyword}、城市={city}，"
                "参数已齐全，应该调用 search_existing_jobs 查库。"
            )
            reasoning_lines.append("查完再把结果整理成岗位卡片返回，并附上原页面链接。")
        else:
            reasoning_lines.append("这不是岗位查询，直接回答即可，不需要调用工具。")

        if is_job:
            reply = (
                f"已经为你检索 **{city}** 的 **{keyword}** 岗位。\n\n"
                "以下是匹配结果，每条都可以点「去 BOSS 沟通」跳转到原页面。\n\n"
                "> 当前为 **P0 骨架模式**（假模型 echo），上面的内容是模拟输出，"
                "用于验证流式链路与界面渲染。接入真实模型后这里会是真正的岗位数据。"
            )
            tool_calls = [
                ToolCall(
                    id="fake-call-1",
                    name="search_existing_jobs",
                    arguments=(
                        '{"keyword": "' + keyword + '", "city": "' + city + '", "limit": 5}'
                    ),
                )
            ]
        else:
            reply = (
                f"收到你的消息：**{user_text[:80]}**\n\n"
                "这是 P0 骨架模式的模拟回复，用于验证：\n\n"
                "1. SSE 流式是否逐字到达；\n"
                "2. 思考过程能否折叠展示；\n"
                "3. 附件（图片/文档）能否正确上传并解析。\n\n"
                "接入真实模型后，这里会是 `deepseek-flash` 生成的正文。"
            )
            tool_calls = []

        return reply, "\n".join(reasoning_lines), tool_calls

    @staticmethod
    def _chunks(text: str, size: int) -> list[str]:
        return [text[i : i + size] for i in range(0, len(text), size)] or [""]

    @staticmethod
    def _estimate(messages: list[Message]) -> int:
        """粗略估算 prompt token（中文按 1 字 ≈ 1 token）。"""
        total = 0
        for message in messages:
            total += len(message.content or "")
            total += len(message.images) * 1024  # 与 DeepSeek 图片上限一致
        return max(total, 1)


__all__ = ["FakeLLMDriver"]
