"""大模型抽象层。

对外只暴露这三件事：

* :func:`get_llm_client` —— 工厂，按数据库配置动态实例化适配器
* :class:`LLMAdapter` —— 适配器接口，统一 ``chat_completion`` / ``stream_chat``
* :class:`LLMError` —— 统一异常，带错误分类与可操作的修复建议

供应商差异全部收敛在 ``adapters/`` 里；新增供应商只改 ``registry.py``，
前端无需改动（供应商元数据由 ``/api/models/providers`` 提供）。
"""

from .base import (
    ImagePart,
    LLMAdapter,
    LLMResponse,
    Message,
    Role,
    StreamChunk,
    StreamEventType,
    TestResult,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)
from .capabilities import Capability, normalize_capabilities
from .errors import LLMError, LLMErrorType
from .factory import (
    assert_config_usable,
    build_adapter,
    clear_client_cache,
    get_default_model_config,
    get_llm_client,
    resolve_model_config,
)
from .registry import get_adapter_class, list_provider_metadata, supported_providers

__all__ = [
    "Capability",
    "ImagePart",
    "LLMAdapter",
    "LLMError",
    "LLMErrorType",
    "LLMResponse",
    "Message",
    "Role",
    "StreamChunk",
    "StreamEventType",
    "TestResult",
    "TokenUsage",
    "ToolCall",
    "ToolDefinition",
    "assert_config_usable",
    "build_adapter",
    "clear_client_cache",
    "get_adapter_class",
    "get_default_model_config",
    "get_llm_client",
    "list_provider_metadata",
    "normalize_capabilities",
    "resolve_model_config",
    "supported_providers",
]
