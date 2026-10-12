"""OpenAI 兼容适配器。

绝大多数供应商（DeepSeek、DashScope 兼容模式、vLLM、OneAPI、各类中转）都提供
OpenAI 兼容的 ``/chat/completions``。把共用逻辑集中在这里，新增这类供应商
只需写一个十几行的子类声明默认值。

使用 ``openai`` 官方异步客户端：省掉手写流式解析与 SSE 分包处理，
且它对 ``httpx`` 的连接池管理更成熟。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ..capabilities import Capability
from ..base import (
    ImagePart,
    LLMAdapter,
    LLMResponse,
    Message,
    Role,
    StreamChunk,
    StreamEventType,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)
from ..errors import LLMError, LLMErrorType, wrap_exception


def _image_to_content_part(image: ImagePart) -> dict[str, Any]:
    """把统一图片结构转成 OpenAI 的 image_url 内容块。"""
    return {
        "type": "image_url",
        "image_url": {"url": image.to_data_url(), "detail": image.detail or "auto"},
    }


def build_openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """统一消息 → OpenAI 消息格式。

    ``content`` 只有在同时含文本与图片时才需要数组形式；纯文本仍用字符串，
    兼容性更好（部分中转服务不接受纯文本的数组形式）。
    """
    payload: list[dict[str, Any]] = []

    for message in messages:
        item: dict[str, Any] = {"role": message.role.value}

        if message.images:
            parts: list[dict[str, Any]] = []
            if message.content:
                parts.append({"type": "text", "text": message.content})
            parts.extend(_image_to_content_part(image) for image in message.images)
            item["content"] = parts
        else:
            item["content"] = message.content or ""

        if message.tool_calls:
            item["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments or "{}"},
                }
                for call in message.tool_calls
            ]
            # OpenAI 要求带 tool_calls 的 assistant 消息 content 可以为 null
            if not message.content:
                item["content"] = None

        if message.tool_call_id:
            item["tool_call_id"] = message.tool_call_id

        payload.append(item)

    return payload


def build_openai_tools(tools: list[ToolDefinition] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                **({"strict": True} if tool.strict else {}),
            },
        }
        for tool in tools
    ]


def _extract_usage(raw: Any) -> TokenUsage:
    """从响应里提取 token 用量，兼容不同供应商的字段命名。"""
    usage = getattr(raw, "usage", None)
    if usage is None and isinstance(raw, dict):
        usage = raw.get("usage")
    if usage is None:
        return TokenUsage()

    def pick(*names: str) -> int:
        for name in names:
            value = getattr(usage, name, None)
            if value is None and isinstance(usage, dict):
                value = usage.get(name)
            if isinstance(value, int):
                return value
        return 0

    cached = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is None and isinstance(usage, dict):
        details = usage.get("prompt_tokens_details")
    if details is not None:
        cached = getattr(details, "cached_tokens", None) or (
            details.get("cached_tokens") if isinstance(details, dict) else 0
        ) or 0
    if not cached:
        cached = pick("prompt_cache_hit_tokens")  # DeepSeek 的字段名

    return TokenUsage(
        prompt_tokens=pick("prompt_tokens", "input_tokens"),
        completion_tokens=pick("completion_tokens", "output_tokens"),
        cached_tokens=int(cached or 0),
    )


def _get_extra_field(obj: Any, name: str) -> str:
    """读取供应商的非标准字段。

    ``reasoning_content`` 是 DeepSeek 的扩展；不同中转服务可能叫
    ``reasoning`` 或塞进 ``model_extra``，这里统一兜一层。
    """
    if obj is None:
        return ""
    value = getattr(obj, name, None)
    if value:
        return str(value)
    extra = getattr(obj, "model_extra", None)
    if isinstance(extra, dict):
        fallback = extra.get(name)
        if fallback:
            return str(fallback)
    return ""


def _parse_tool_calls(raw_calls: Any) -> list[ToolCall]:
    """解析非流式响应里的 tool_calls。"""
    result: list[ToolCall] = []
    for call in raw_calls or []:
        function = getattr(call, "function", None)
        if function is None and isinstance(call, dict):
            function = call.get("function") or {}
        name = getattr(function, "name", None) or (
            function.get("name") if isinstance(function, dict) else None
        )
        arguments = getattr(function, "arguments", None) or (
            function.get("arguments") if isinstance(function, dict) else None
        )
        call_id = getattr(call, "id", None) or (call.get("id") if isinstance(call, dict) else None)
        if name:
            result.append(
                ToolCall(id=call_id or f"call_{len(result)}", name=name, arguments=arguments or "{}")
            )
    return result


class OpenAICompatAdapter(LLMAdapter):
    """OpenAI 兼容协议的通用实现。"""

    provider = "openai_compat"
    display_name = "OpenAI 兼容"
    default_base_url = "https://api.openai.com/v1"
    requires_api_key = True
    supported_capabilities = frozenset(
        {Capability.TEXT.value, Capability.VISION.value, Capability.TOOL_CALLS.value}
    )
    doc_url = "https://platform.openai.com/docs/api-reference/chat"

    # ------------------------------------------------------------------
    def _client(self):
        """构造异步客户端。延迟 import，避免未安装时影响其他模块。"""
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - 依赖缺失时的明确提示
            raise LLMError(
                LLMErrorType.CONFIG_ERROR,
                "缺少 openai 依赖",
                detail="请在后端环境执行 pip install openai 后重启服务。",
            ) from exc

        kwargs: dict[str, Any] = {
            "api_key": self.api_key or "not-required",
            "timeout": self.timeout,
            "max_retries": 1,  # 重试交给上层统一控制，避免叠加放大
        }
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return AsyncOpenAI(**kwargs)

    def _request_kwargs(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        self.assert_ready()
        self.validate_messages(messages)

        required: set[str] = set()
        for message in messages:
            required |= message.required_capabilities
        if tools:
            required.add(Capability.TOOL_CALLS.value)
        self.assert_capabilities(required)

        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": build_openai_messages(messages),
        }
        openai_tools = build_openai_tools(tools)
        if openai_tools:
            payload["tools"] = openai_tools
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        payload.update(self.extra_config)
        return payload

    # ------------------------------------------------------------------
    async def chat_completion(
        self,
        messages: list[Message],
        *,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        payload = self._request_kwargs(messages, tools, temperature, max_tokens)
        client = self._client()
        try:
            response = await client.chat.completions.create(**payload)
        except Exception as exc:  # noqa: BLE001 - 统一包装成 LLMError
            raise wrap_exception(exc, context=f"{self.provider} chat_completion") from exc

        choice = response.choices[0] if response.choices else None
        if choice is None:
            raise LLMError(
                LLMErrorType.BAD_RESPONSE,
                "接口未返回任何结果",
                detail="响应中没有 choices，可能是 Base URL 指向了非兼容接口。",
            )

        message = getattr(choice, "message", None)
        # 推理模型（如 DeepSeek V4.1 Flash）会把思考过程放在 reasoning_content，
        # 正文放在 content。两者都要取，否则界面上会出现"只有思考没有回答"。
        reasoning = ""
        if message is not None:
            reasoning = (
                getattr(message, "reasoning_content", None)
                or _get_extra_field(message, "reasoning")
                or ""
            )
        return LLMResponse(
            text=(getattr(message, "content", None) or "") if message else "",
            reasoning=reasoning,
            tool_calls=_parse_tool_calls(getattr(message, "tool_calls", None)),
            finish_reason=getattr(choice, "finish_reason", None) or "stop",
            usage=_extract_usage(response),
            model=getattr(response, "model", None) or self.model_id,
            raw=response,
        )

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        payload = self._request_kwargs(messages, tools, temperature, max_tokens)
        payload["stream"] = True
        # 让供应商在最后一个 chunk 里带上用量统计
        payload.setdefault("stream_options", {"include_usage": True})

        client = self._client()
        try:
            stream = await client.chat.completions.create(**payload)
        except Exception as exc:  # noqa: BLE001
            raise wrap_exception(exc, context=f"{self.provider} stream_chat") from exc

        # 工具调用是分片下发的，按 index 累积后再吐完整调用
        pending: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None

        try:
            async for chunk in stream:
                usage = _extract_usage(chunk)
                if usage.total_tokens or usage.cached_tokens:
                    yield StreamChunk(type=StreamEventType.USAGE, usage=usage)

                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = getattr(choice, "delta", None)
                if choice.finish_reason:
                    finish_reason = choice.finish_reason

                if delta is None:
                    continue

                text = getattr(delta, "content", None)
                if text:
                    yield StreamChunk(type=StreamEventType.TEXT, text=text)

                # 推理模型的思考过程单独成流：界面上应折叠展示，
                # 否则正文开始前用户会面对一段时间的空屏。
                reasoning = _get_extra_field(delta, "reasoning_content") or _get_extra_field(
                    delta, "reasoning"
                )
                if reasoning:
                    yield StreamChunk(type=StreamEventType.REASONING, text=reasoning)

                for call in getattr(delta, "tool_calls", None) or []:
                    index = getattr(call, "index", 0) or 0
                    slot = pending.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    if getattr(call, "id", None):
                        slot["id"] = call.id
                    function = getattr(call, "function", None)
                    if function is not None:
                        if getattr(function, "name", None):
                            slot["name"] = function.name
                        if getattr(function, "arguments", None):
                            slot["arguments"] += function.arguments
        except Exception as exc:  # noqa: BLE001
            raise wrap_exception(exc, context=f"{self.provider} stream_chat") from exc

        for index in sorted(pending):
            slot = pending[index]
            if not slot["name"]:
                continue
            yield StreamChunk(
                type=StreamEventType.TOOL_CALL,
                tool_call=ToolCall(
                    id=slot["id"] or f"call_{index}",
                    name=slot["name"],
                    arguments=slot["arguments"] or "{}",
                ),
            )

        yield StreamChunk(type=StreamEventType.DONE, finish_reason=finish_reason or "stop")


__all__ = [
    "OpenAICompatAdapter",
    "build_openai_messages",
    "build_openai_tools",
]
