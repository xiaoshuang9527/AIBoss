"""供应商注册表。

**新增一个供应商只需要两步**：
1. 在 ``adapters/`` 下写一个适配器类；
2. 在下面的 ``_ADAPTERS`` 里注册。

前端不需要任何改动 —— 供应商下拉、默认地址、是否要 Key、支持哪些能力
全部来自 ``/api/models/providers``，数据源就是这个注册表。
"""

from __future__ import annotations

from .adapters.deepseek import DeepSeekAdapter
from .adapters.openai_compat import OpenAICompatAdapter
from .base import LLMAdapter

#: 已实现的适配器。**只注册真正可用的**，未实现的不列出来，
#: 避免用户在下拉里选到一个必然失败的项目。
_ADAPTERS: tuple[type[LLMAdapter], ...] = (
    DeepSeekAdapter,
    OpenAICompatAdapter,
)

_REGISTRY: dict[str, type[LLMAdapter]] = {item.provider: item for item in _ADAPTERS}


def get_adapter_class(provider: str) -> type[LLMAdapter]:
    """按供应商标识取适配器类。"""
    key = (provider or "").strip().lower()
    if key not in _REGISTRY:
        from .errors import LLMError, LLMErrorType

        available = "、".join(sorted(_REGISTRY)) or "（无）"
        raise LLMError(
            LLMErrorType.CONFIG_ERROR,
            f"不支持的供应商：{provider or '(空)'}",
            detail=f"当前已实现的供应商有：{available}",
        )
    return _REGISTRY[key]


def list_provider_metadata() -> list[dict]:
    """给前端的供应商清单（含能力、默认地址、是否需要 Key）。"""
    return [cls.metadata() for cls in sorted(_ADAPTERS, key=lambda item: item.display_name)]


def supported_providers() -> list[str]:
    return sorted(_REGISTRY)


def is_registered(provider: str) -> bool:
    return (provider or "").strip().lower() in _REGISTRY


__all__ = [
    "get_adapter_class",
    "is_registered",
    "list_provider_metadata",
    "supported_providers",
]
