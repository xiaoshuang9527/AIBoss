"""适配器抽象层：统一的数据结构与供应商接口。

所有供应商适配器实现 :class:`LLMAdapter`，对外只暴露
``chat_completion`` / ``stream_chat`` / ``test_connection`` 三个方法，
上层（对话模块、测试连接接口）不需要知道任何供应商差异。

数据结构刻意保持**与 OpenAI 格式解耦**：这样将来要换 LiteLLM 或加
非 OpenAI 风格的供应商（Gemini 原生 API），上层代码不用动。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .capabilities import Capability


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ImagePart:
    """一张待输入的图片。

    统一用 base64 承载，因为大多数场景下图片是用户刚上传的本地文件，
    没有公网 URL 可用；需要 URL 的供应商由适配器自行转换。
    """

    data: str                       # base64（不含 data: 前缀）
    mime_type: str                  # image/jpeg / image/png / image/gif / image/webp
    detail: str = "auto"            # auto / low / high / original

    def to_data_url(self) -> str:
        return f"data:{self.mime_type};base64,{self.data}"


@dataclass
class ToolCall:
    """模型发起的一次工具调用。"""

    id: str
    name: str
    arguments: str = "{}"           # 原始 JSON 字符串（流式拼接期间可能不完整）

    def parsed_arguments(self) -> dict[str, Any]:
        import json

        try:
            value = json.loads(self.arguments or "{}")
        except (ValueError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}


@dataclass
class ToolDefinition:
    """暴露给模型的工具声明（JSON Schema）。"""

    name: str
    description: str
    parameters: dict[str, Any]
    strict: bool = False


@dataclass
class Message:
    """一条对话消息。"""

    role: Role
    content: str | None = None
    images: list[ImagePart] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None

    @classmethod
    def system(cls, text: str) -> "Message":
        return cls(role=Role.SYSTEM, content=text)

    @classmethod
    def user(cls, text: str | None = None, images: list[ImagePart] | None = None) -> "Message":
        return cls(role=Role.USER, content=text, images=list(images or []))

    @classmethod
    def assistant(cls, text: str | None = None, tool_calls: list[ToolCall] | None = None) -> "Message":
        return cls(role=Role.ASSISTANT, content=text, tool_calls=list(tool_calls or []))

    @classmethod
    def tool_result(cls, tool_call_id: str, content: str) -> "Message":
        return cls(role=Role.TOOL, content=content, tool_call_id=tool_call_id)

    @property
    def required_capabilities(self) -> set[str]:
        """本条消息隐含需要的能力。"""
        required: set[str] = set()
        if self.images:
            required.add(Capability.VISION.value)
        if self.tool_calls:
            required.add(Capability.TOOL_CALLS.value)
        return required


@dataclass
class TokenUsage:
    """token 用量。``cached_tokens`` 单独记录，因为缓存命中单价更低。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    #: 输出里属于「思考过程」的部分（推理模型才有）。
    #:
    #: 为什么要单独记：``deepseek-flash`` 这类推理模型，思考阶段的输出经常
    #: 比正文还多（实测一次回答 68 个思考分片 vs 9 个正文分片）。
    #: 混在 completion_tokens 里看不出问题，分开记才能回答
    #: "我的 token 到底花在哪了" —— 这正是成本看板要回答的问题。
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class LLMResponse:
    """一次完整（非流式）调用的结果。"""

    text: str = ""
    #: 推理模型的思考过程（DeepSeek 的 ``reasoning_content``）。
    #: 与 :attr:`text` 分开保存，便于界面折叠展示"思考中/已深度思考"。
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str | None = None
    raw: Any = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class StreamEventType(str, Enum):
    """流式事件的类型。上层按类型分发到前端 SSE。"""

    TEXT = "text"                 # 正文增量
    REASONING = "reasoning"       # 思考过程增量（推理模型）
    TOOL_CALL = "tool_call"       # 工具调用（增量或完整）
    USAGE = "usage"               # 用量统计（通常在末尾）
    DONE = "done"                 # 结束


@dataclass
class StreamChunk:
    """流式返回的一个片段。"""

    type: StreamEventType
    text: str = ""
    tool_call: ToolCall | None = None
    usage: TokenUsage | None = None
    finish_reason: str | None = None


@dataclass
class TestResult:
    """连通性测试结果。"""

    ok: bool
    latency_ms: int = 0
    model_echo: str | None = None      # 供应商回显的模型名，用来确认真的通了
    message: str = ""
    error_type: str | None = None
    suggestion: str | None = None
    detail: str | None = None


class LLMAdapter(ABC):
    """供应商适配器基类。

    子类至少要声明 :attr:`provider` / :attr:`supported_capabilities`，
    并实现 :meth:`chat_completion` 与 :meth:`stream_chat`。
    """

    #: 供应商标识，与 ``ModelConfig.provider`` 对应
    provider: str = ""
    #: 展示名
    display_name: str = ""
    #: 该供应商默认的 API 地址（用户不填 base_url 时使用）
    default_base_url: str | None = None
    #: 是否需要 API Key（Ollama 等本地部署不需要）
    requires_api_key: bool = True
    #: 该供应商**天生**支持的能力。注意这是上限，具体模型可能更少，
    #: 因此模型配置里的 capabilities 仍需用户确认。
    supported_capabilities: frozenset[str] = frozenset({Capability.TEXT.value})
    #: 是否需要填写 base_url（False 表示有唯一默认值，不可改）
    base_url_editable: bool = True
    #: 文档地址，前端展示用
    doc_url: str | None = None
    #: 供应商特有参数的说明，前端据此渲染扩展表单
    extra_fields: tuple[dict[str, Any], ...] = ()

    def __init__(
        self,
        *,
        model_id: str,
        api_key: str | None = None,
        base_url: str | None = None,
        capabilities: list[str] | None = None,
        extra_config: dict[str, Any] | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.model_id = model_id
        self.api_key = api_key
        self.base_url = (base_url or self.default_base_url or "").rstrip("/")
        self.capabilities = list(capabilities or [])
        self.extra_config = dict(extra_config or {})
        self.timeout = timeout

    # ------------------------------------------------------------------
    # 能力校验
    # ------------------------------------------------------------------
    def assert_capabilities(self, required: set[str]) -> None:
        """前置校验所需能力；缺失时抛 :class:`LLMError`。

        宁可在这里给出清晰报错，也不要等供应商 API 返回一个含糊的 400。
        """
        if not required:
            return
        from .errors import LLMError, LLMErrorType

        missing = {item for item in required if item not in set(self.capabilities)}
        if missing:
            from .capabilities import Capability

            labels = []
            for item in sorted(missing):
                try:
                    labels.append(Capability(item).label)
                except ValueError:
                    labels.append(item)
            raise LLMError(
                LLMErrorType.UNSUPPORTED_CAPABILITY,
                f"模型 {self.model_id} 未声明能力：{'、'.join(labels)}",
                detail=(
                    "请在「模型管理」中为该模型勾选对应能力标签，"
                    "或改用支持该能力的模型。"
                ),
            )

    def assert_ready(self) -> None:
        """校验配置完整性。"""
        from .errors import LLMError, LLMErrorType

        if not self.model_id:
            raise LLMError(LLMErrorType.NOT_CONFIGURED, "未填写模型 ID")
        if self.requires_api_key and not self.api_key:
            raise LLMError(
                LLMErrorType.NOT_CONFIGURED,
                "未配置 API Key",
                detail="该供应商需要 API Key，请在模型配置里填写后重试。",
            )
        if not self.base_url:
            raise LLMError(
                LLMErrorType.NOT_CONFIGURED,
                "未配置 API 地址",
                detail=f"供应商 {self.provider} 没有默认地址，请手动填写 Base URL。",
            )

    def validate_messages(self, messages: list[Message]) -> None:
        """消息序列合法性校验，子类可覆写以加入供应商特有约束。"""

    # ------------------------------------------------------------------
    # 子类必须实现
    # ------------------------------------------------------------------
    @abstractmethod
    async def chat_completion(
        self,
        messages: list[Message],
        *,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """一次性返回完整结果。"""

    @abstractmethod
    def stream_chat(
        self,
        messages: list[Message],
        *,
        tools: list[ToolDefinition] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """流式返回。注意：返回的是异步迭代器，不是协程。"""

    # ------------------------------------------------------------------
    # 默认实现
    # ------------------------------------------------------------------
    async def test_connection(self) -> TestResult:
        """默认探测：发一条极简消息，看是否返回可用结果。

        子类可覆写为更轻量的探测（例如只列模型列表），以减少 token 消耗。
        """
        import time

        from .errors import LLMError, wrap_exception

        started = time.monotonic()
        try:
            self.assert_ready()
            response = await self.chat_completion(
                [Message.user("ping")],
                max_tokens=8,
                temperature=0.0,
            )
        except LLMError as exc:
            return TestResult(
                ok=False,
                latency_ms=int((time.monotonic() - started) * 1000),
                message=exc.title,
                error_type=exc.error_type.value,
                suggestion=exc.suggestion,
                detail=exc.detail,
            )
        except Exception as exc:  # noqa: BLE001 - 测试连接不应向外抛异常
            wrapped = wrap_exception(exc, context="测试连接")
            return TestResult(
                ok=False,
                latency_ms=int((time.monotonic() - started) * 1000),
                message=wrapped.title,
                error_type=wrapped.error_type.value,
                suggestion=wrapped.suggestion,
                detail=wrapped.detail,
            )

        return TestResult(
            ok=True,
            latency_ms=int((time.monotonic() - started) * 1000),
            model_echo=response.model or self.model_id,
            message="连接正常",
        )

    # ------------------------------------------------------------------
    # 元数据
    # ------------------------------------------------------------------
    @classmethod
    def metadata(cls) -> dict[str, Any]:
        """给前端的供应商元数据。"""
        return {
            "provider": cls.provider,
            "display_name": cls.display_name or cls.provider,
            "default_base_url": cls.default_base_url,
            "requires_api_key": cls.requires_api_key,
            "base_url_editable": cls.base_url_editable,
            "supported_capabilities": sorted(cls.supported_capabilities),
            "doc_url": cls.doc_url,
            "extra_fields": list(cls.extra_fields),
        }


__all__ = [
    "ImagePart",
    "LLMAdapter",
    "LLMResponse",
    "Message",
    "Role",
    "StreamChunk",
    "StreamEventType",
    "TestResult",
    "TokenUsage",
    "ToolCall",
    "ToolDefinition",
]
