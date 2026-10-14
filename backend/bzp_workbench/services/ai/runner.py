"""工具调用循环（Agent Runner）。

这是 P1 的核心：把「模型 → 工具 → 模型」的循环、SSE 事件分发、熔断保护
和上下文管理集中在一处，让上层（service）只需要消费事件。

为什么要熔断
------------
实测模型会主动调用工具。如果不限制轮数，模型可能反复查询同一份数据
（尤其在返回 0 条时更容易「再试一次」），既烧 token 又让用户干等。
所以硬性限制最多 :data:`MAX_TOOL_ROUNDS` 轮，达到上限后强制让模型收尾。

为什么能力守卫要前置
--------------------
DeepSeek 规定图片只能放在 user 消息里，且模型必须声明 vision 能力才能收图。
如果等到 API 返回 400 再告诉用户，错误信息会很难懂。这里在发请求前就检查，
并给出明确的修复建议（去模型管理里勾选能力，或换模型）。
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from ..llm import (
    LLMError,
    LLMErrorType,
    LLMResponse,
    Message,
    StreamEventType,
    ToolCall,
    wrap_exception,
)
from ..logging_service import logger
from .stream import SSEEvent, SSEFrame
from .tools import get_tool, tools_for_mode, ToolError, ToolContext, ToolResult

#: 单次回复最多允许的「模型→工具→模型」轮数
MAX_TOOL_ROUNDS = 3

#: 送入模型的最大历史消息条数
MAX_HISTORY_MESSAGES = 40


@dataclass
class TurnResult:
    """一轮完整交互（可能包含多次工具调用）的结果。"""

    text: str = ""
    reasoning: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    #: 输出里属于「思考过程」的部分（推理模型才有）。
    #: 分开记才能回答"token 花在思考还是正文上"。
    reasoning_tokens: int = 0
    finish_reason: str = "stop"
    latency_ms: int = 0
    error_type: str | None = None
    error_message: str | None = None
    error_suggestion: str | None = None


def trim_history(messages: list[Message], limit: int = MAX_HISTORY_MESSAGES) -> list[Message]:
    """裁剪历史，保留 system 消息与最近若干条。

    直接按条数截断，不按 token 精确计算 —— 这在本场景够用，
    而且避免了引入 tokenizer 依赖。system 消息必须保留，否则模型会失忆。
    """
    if len(messages) <= limit:
        return messages

    systems = [item for item in messages if item.role.value == "system"]
    others = [item for item in messages if item.role.value != "system"]
    keep = others[-(limit - len(systems)) :] if len(systems) < limit else others[:0]
    return systems + keep


def guard_capabilities(
    model_capabilities: list[str],
    *,
    has_images: bool,
    has_tools: bool,
    model_label: str,
) -> None:
    """发请求前校验模型能力，缺失时抛可读的 :class:`LLMError`。"""
    missing: list[str] = []
    if has_images and "vision" not in model_capabilities:
        missing.append("看图（vision）")
    if has_tools and "tool_calls" not in model_capabilities:
        missing.append("工具调用（tool_calls）")

    if not missing:
        return

    raise LLMError(
        LLMErrorType.UNSUPPORTED_CAPABILITY,
        f"当前模型「{model_label}」缺少能力：{'、'.join(missing)}",
        detail=(
            "请到「系统设置 → 模型管理」为该模型勾选对应能力标签，"
            "或在对话页顶部切换到支持该能力的模型。"
            + ("（你上传了图片，需要支持「看图」的模型）" if has_images else "")
        ),
    )


async def run_turn(
    adapter: Any,
    *,
    messages: list[Message],
    mode: str,
    result: TurnResult,
    tool_context: ToolContext | None = None,
    model_label: str = "",
    model_capabilities: list[str] | None = None,
    max_tokens: int | None = None,
) -> AsyncIterator[SSEFrame]:
    """执行一轮交互，逐帧产出 SSE 内容事件。

    产出的事件序列：
    ``reasoning*`` → ``text*`` → (``tool_call`` → ``tool_result`` → ``text*``)... → ``error?``

    **本函数只产出内容事件，不产出 usage / done** —— 这两个是终止事件，
    由调用方在拿到完整的 :class:`TurnResult` 后发出。这样职责清晰：
    runner 负责「循环与内容」，service 负责「收尾与落库」。

    ``result`` 由调用方创建并传入，函数就地填充。**刻意不用全局变量或返回值**：
    生成器无法 `return` 值，而全局可变状态在并发请求下会互相串数据。

    注意：本函数**不抛异常**。所有失败都转成 ``error`` 事件，
    因为调用方（StreamingResponse）已经开始发送响应体了。
    """
    started = time.monotonic()
    capabilities = list(model_capabilities or [])

    working = trim_history(list(messages))
    tools = tools_for_mode(mode)
    tool_definitions = [tool.to_definition() for tool in tools] or None

    has_images = any(message.images for message in working)

    # ---- 能力守卫 ----
    try:
        guard_capabilities(
            capabilities,
            has_images=has_images,
            has_tools=bool(tool_definitions),
            model_label=model_label or "当前模型",
        )
    except LLMError as exc:
        result.error_type = exc.error_type.value
        result.error_message = exc.message
        result.error_suggestion = exc.suggestion
        result.latency_ms = int((time.monotonic() - started) * 1000)
        yield SSEFrame(SSEEvent.ERROR, exc.to_payload())
        return

    seen_calls: set[str] = set()

    for round_index in range(MAX_TOOL_ROUNDS + 1):
        # 最后一轮不再把工具交给模型，强制它基于已有信息作答
        round_tools = tool_definitions if round_index < MAX_TOOL_ROUNDS else None
        if round_index == MAX_TOOL_ROUNDS and tool_definitions:
            logger.info(
                "工具调用达到上限 %s 轮，强制收尾（mode=%s）", MAX_TOOL_ROUNDS, mode
            )

        round_text = ""
        round_reasoning = ""
        round_calls: list[ToolCall] = []

        try:
            async for chunk in adapter.stream_chat(
                working,
                tools=round_tools,
                max_tokens=max_tokens,
            ):
                if chunk.type is StreamEventType.REASONING:
                    round_reasoning += chunk.text
                    yield SSEFrame(SSEEvent.REASONING, {"delta": chunk.text})
                elif chunk.type is StreamEventType.TEXT:
                    round_text += chunk.text
                    yield SSEFrame(SSEEvent.TEXT, {"delta": chunk.text})
                elif chunk.type is StreamEventType.TOOL_CALL and chunk.tool_call:
                    round_calls.append(chunk.tool_call)
                elif chunk.type is StreamEventType.USAGE and chunk.usage:
                    # 多轮时累加，否则只会记下最后一轮的用量
                    result.prompt_tokens += chunk.usage.prompt_tokens
                    result.completion_tokens += chunk.usage.completion_tokens
                    result.cached_tokens += chunk.usage.cached_tokens
                    result.reasoning_tokens += chunk.usage.reasoning_tokens
                elif chunk.type is StreamEventType.DONE:
                    result.finish_reason = chunk.finish_reason or "stop"
        except LLMError as exc:
            result.error_type = exc.error_type.value
            result.error_message = exc.message
            result.error_suggestion = exc.suggestion
            yield SSEFrame(SSEEvent.ERROR, exc.to_payload())
            break
        except Exception as exc:  # noqa: BLE001
            wrapped = wrap_exception(exc, context="模型流式调用")
            logger.exception("模型流式调用失败：%s", exc)
            result.error_type = wrapped.error_type.value
            result.error_message = wrapped.message
            result.error_suggestion = wrapped.suggestion
            yield SSEFrame(SSEEvent.ERROR, wrapped.to_payload())
            break

        result.text += round_text
        result.reasoning += round_reasoning

        # ---- 没有工具调用 → 结束 ----
        if not round_calls:
            break

        # ---- 去重：同一「工具+参数」重复调用直接跳过，避免无意义循环 ----
        fresh_calls: list[ToolCall] = []
        for call in round_calls:
            signature = f"{call.name}:{call.arguments}"
            if signature in seen_calls:
                logger.info("跳过重复的工具调用：%s", signature[:120])
                continue
            seen_calls.add(signature)
            fresh_calls.append(call)

        if not fresh_calls:
            result.finish_reason = "stop"
            break

        # 把模型这一轮的输出（含工具调用）加入上下文
        working.append(Message.assistant(round_text or None, round_calls))

        for call in fresh_calls:
            arguments = call.parsed_arguments()
            result.tool_calls.append(
                {"id": call.id, "name": call.name, "arguments": arguments}
            )
            yield SSEFrame(
                SSEEvent.TOOL_CALL,
                {"id": call.id, "name": call.name, "arguments": arguments},
            )

            tool_result = await _execute_tool(call.name, arguments, tool_context)
            payload = tool_result.to_payload() if tool_result else {}
            result.tool_results.append(
                {
                    "name": call.name,
                    "ok": tool_result is not None and payload.get("payload_type") != "text",
                    **payload,
                }
            )

            if tool_result is not None:
                yield SSEFrame(
                    SSEEvent.TOOL_RESULT,
                    {"id": call.id, "name": call.name, **payload},
                )

            # 工具结果回填给模型。失败也要回填，让它知道原因才能换策略。
            working.append(
                Message.tool_result(
                    call.id,
                    tool_result.model_text if tool_result else "工具不可用。",
                )
            )

    result.latency_ms = int((time.monotonic() - started) * 1000)


async def _execute_tool(
    name: str, arguments: dict[str, Any], context: ToolContext | None
) -> ToolResult | None:
    """执行工具，把各类失败都转成可读文本回填给模型。"""
    if context is None:
        return ToolResult(
            payload_type="text",
            data=None,
            model_text="工具暂时不可用（缺少执行上下文）。",
        )

    tool = get_tool(name)
    if tool is None:
        return ToolResult(
            payload_type="text",
            data=None,
            model_text=(
                f"不存在名为 {name} 的工具。"
                f"可用工具：{', '.join(item.name for item in tools_for_mode('chat'))}"
            ),
        )

    started = time.monotonic()
    try:
        return await tool.run(arguments, context)
    except ToolError as exc:
        logger.info("工具 %s 执行失败：%s", name, exc.message)
        return ToolResult(
            payload_type="text",
            data=None,
            model_text=exc.to_model_text(),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("工具 %s 抛出未预期异常", name)
        return ToolResult(
            payload_type="text",
            data=None,
            model_text=(
                f"工具 {name} 执行出错：{type(exc).__name__}: {exc}。"
                "请根据这个错误调整策略，或直接告诉用户稍后重试。"
            ),
            latency_ms=int((time.monotonic() - started) * 1000),
        )


__all__ = [
    "MAX_HISTORY_MESSAGES",
    "MAX_TOOL_ROUNDS",
    "TurnResult",
    "guard_capabilities",
    "run_turn",
    "trim_history",
]
