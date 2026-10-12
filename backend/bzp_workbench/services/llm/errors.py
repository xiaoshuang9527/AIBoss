"""大模型调用的统一异常与错误分类。

为什么要分这么细
----------------
「测试连接」是用户唯一能自己判断配置对不对的手段。如果所有失败都抛一句
"连接失败"，用户无法区分是 Key 错了、模型 ID 拼错了、还是 Base URL 填错了。
这里把失败归成有限的几类，每类都能给出**可操作的**修复建议。
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class LLMErrorType(str, Enum):
    """错误分类。前端按 type 决定提示样式与文案。"""

    AUTH_FAILED = "auth_failed"
    """401/403：Key 无效、过期或权限不足。"""

    MODEL_NOT_FOUND = "model_not_found"
    """404：模型 ID 不存在或拼写错误。"""

    RATE_LIMITED = "rate_limited"
    """429：触发限流或余额不足。"""

    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    """本地前置校验失败：该模型不具备本次调用所需能力。"""

    INVALID_MESSAGES = "invalid_messages"
    """消息序列不合法（例如把图片放进了 system 消息）。"""

    NETWORK_ERROR = "network_error"
    """DNS 解析失败、连接超时、TLS 错误。"""

    BAD_RESPONSE = "bad_response"
    """HTTP 200 但响应结构不符合预期，通常说明 Base URL 指向的不是兼容接口。"""

    NOT_CONFIGURED = "not_configured"
    """模型未配置完整（缺 Key 或 Base URL）。"""

    CONFIG_ERROR = "config_error"
    """配置本身有问题，例如密钥无法解密。"""

    UNKNOWN = "unknown"


# 每类错误的默认用户提示与修复建议
_ERROR_HINTS: dict[LLMErrorType, tuple[str, str]] = {
    LLMErrorType.AUTH_FAILED: (
        "认证失败",
        "请检查 API Key 是否正确、是否已过期，以及该 Key 是否有调用此模型的权限。",
    ),
    LLMErrorType.MODEL_NOT_FOUND: (
        "模型不存在",
        "请检查「模型 ID」是否拼写正确（例如 DeepSeek 要用 deepseek-flash）。",
    ),
    LLMErrorType.RATE_LIMITED: (
        "请求被限流",
        "触发了供应商的速率限制或账户余额不足，请稍后重试或检查账户状态。",
    ),
    LLMErrorType.UNSUPPORTED_CAPABILITY: (
        "模型能力不匹配",
        "当前模型未声明所需能力，请在模型配置里补充能力标签，或改用其他模型。",
    ),
    LLMErrorType.INVALID_MESSAGES: (
        "消息格式不被支持",
        "该供应商对消息结构有限制（例如图片只能放在 user 消息里），请调整输入。",
    ),
    LLMErrorType.NETWORK_ERROR: (
        "网络连接失败",
        "无法连接到 API 地址，请检查 Base URL 是否正确、本机网络与代理设置。",
    ),
    LLMErrorType.BAD_RESPONSE: (
        "响应格式异常",
        "接口有响应但内容不符合预期，通常说明 Base URL 指向的不是 OpenAI 兼容接口。",
    ),
    LLMErrorType.NOT_CONFIGURED: (
        "模型未配置完整",
        "请先补全该模型的 API Key 或 API 地址。",
    ),
    LLMErrorType.CONFIG_ERROR: (
        "配置有误",
        "模型配置存在问题，请检查后重试。",
    ),
    LLMErrorType.UNKNOWN: (
        "调用失败",
        "发生了未预期的错误，详情见下方原始信息。",
    ),
}


class LLMError(Exception):
    """大模型调用相关的统一异常。"""

    def __init__(
        self,
        error_type: LLMErrorType,
        message: str | None = None,
        *,
        detail: str | None = None,
        status_code: int | None = None,
        retryable: bool = False,
        raw: Any = None,
    ) -> None:
        self.error_type = error_type
        self.title, self.suggestion = _ERROR_HINTS.get(
            error_type, _ERROR_HINTS[LLMErrorType.UNKNOWN]
        )
        self.message = message or self.title
        self.detail = detail
        self.status_code = status_code
        self.retryable = retryable
        self.raw = raw
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        """转成 API 返回结构。"""
        return {
            "error_type": self.error_type.value,
            "title": self.title,
            "message": self.message,
            "suggestion": self.suggestion,
            "detail": self.detail,
            "status_code": self.status_code,
            "retryable": self.retryable,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<LLMError {self.error_type.value}: {self.message}>"


def classify_http_status(status_code: int) -> LLMErrorType:
    """把 HTTP 状态码映射成错误类型。"""
    if status_code in (401, 403):
        return LLMErrorType.AUTH_FAILED
    if status_code == 404:
        return LLMErrorType.MODEL_NOT_FOUND
    if status_code == 429:
        return LLMErrorType.RATE_LIMITED
    if status_code >= 500:
        return LLMErrorType.NETWORK_ERROR
    return LLMErrorType.BAD_RESPONSE


def classify_exception(exc: BaseException) -> LLMErrorType:
    """把底层库抛出的异常映射成错误类型。

    这里刻意**按异常类名做字符串匹配**而不是 import 具体异常类型：
    适配器可能用不同版本的 openai / httpx，避免因版本差异导致分类失效。
    """
    name = type(exc).__name__.lower()
    text = str(exc).lower()

    if "authentication" in name or "permission" in name or "unauthorized" in name:
        return LLMErrorType.AUTH_FAILED
    if "notfound" in name or "not_found" in name:
        return LLMErrorType.MODEL_NOT_FOUND
    if "ratelimit" in name or "rate_limit" in name:
        return LLMErrorType.RATE_LIMITED
    if "timeout" in name or "connect" in name or "network" in name:
        return LLMErrorType.NETWORK_ERROR
    if "apistatus" in name or "httpstatus" in name:
        # 兜底：从文本里捞状态码
        for code in (401, 403, 404, 429):
            if str(code) in text:
                return classify_http_status(code)
        return LLMErrorType.BAD_RESPONSE

    if "api key" in text or "unauthorized" in text or "invalid key" in text:
        return LLMErrorType.AUTH_FAILED
    if "model" in text and ("not found" in text or "does not exist" in text):
        return LLMErrorType.MODEL_NOT_FOUND
    if "rate limit" in text or "too many requests" in text:
        return LLMErrorType.RATE_LIMITED
    if "timed out" in text or "getaddrinfo" in text or "connection" in text:
        return LLMErrorType.NETWORK_ERROR

    return LLMErrorType.UNKNOWN


def wrap_exception(exc: BaseException, *, context: str = "") -> LLMError:
    """把任意底层异常包装成 :class:`LLMError`。"""
    if isinstance(exc, LLMError):
        return exc

    error_type = classify_exception(exc)
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
    if status_code is not None:
        error_type = classify_http_status(int(status_code))

    detail = f"{context}：{type(exc).__name__}: {exc}" if context else f"{type(exc).__name__}: {exc}"
    return LLMError(
        error_type,
        detail=detail,
        status_code=int(status_code) if status_code is not None else None,
        retryable=error_type in (LLMErrorType.RATE_LIMITED, LLMErrorType.NETWORK_ERROR),
    )


__all__ = [
    "LLMError",
    "LLMErrorType",
    "classify_exception",
    "classify_http_status",
    "wrap_exception",
]
