"""动态客户端工厂。

对话接口 / 测试连接接口都通过 :func:`get_llm_client` 拿到适配器实例，
而不是在任何地方写死模型、供应商或地址。

缓存策略
--------
每次请求都 new 一个 AsyncOpenAI 会丢掉底层 httpx 连接池，长对话下开销明显。
这里按 ``(配置 id, updated_at, 解密后密钥指纹)`` 做缓存：

* 模型配置一改，``updated_at`` 变化 → 缓存自然失效；
* ``BZP_SECRET_KEY`` 变更导致密钥不同 → 指纹变化，也会失效；
* 缓存容量固定，避免长期运行下无界增长。
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import ModelConfig
from .base import LLMAdapter
from .capabilities import normalize_capabilities
from .crypto import ApiKeyDecryptError, decrypt_api_key
from .errors import LLMError, LLMErrorType
from .registry import get_adapter_class

# 缓存上限（模型数量通常个位数，64 足够且不会无界增长）
_CACHE_LIMIT = 64
_client_cache: "OrderedDict[tuple, LLMAdapter]" = OrderedDict()


def parse_extra_config(raw: str | None) -> dict[str, Any]:
    """把 ``extra_config`` 的 JSON 文本解析成字典；非法输入返回空字典。

    供应商特有参数（如 Ollama 的 ``keep_alive``、部分中转的 ``top_p``）
    都走这里，这样新增供应商不需要加数据库列。
    """
    if not raw:
        return {}
    import json

    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _cache_key(config: ModelConfig, api_key: str | None) -> tuple[Any, ...]:
    key_fingerprint = hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()[:16]
    return (
        config.id,
        str(config.updated_at),
        config.provider,
        config.model_id,
        config.base_url or "",
        config.capabilities or "",
        key_fingerprint,
    )


def clear_client_cache() -> None:
    """清空缓存（模型增删改后调用，保证配置立即生效）。"""
    _client_cache.clear()


def build_adapter(config: ModelConfig, api_key: str | None = None) -> LLMAdapter:
    """由数据库配置构造适配器（**不查缓存**）。

    传入 ``api_key`` 时优先使用（用于「未保存先测试」的场景）。
    """
    if not config.is_enabled and api_key is None:
        # 注意：这里不直接报错，因为测试连接允许测未启用的模型。
        # 真正拦截放在调用路径上，见 assert_config_usable()。
        pass

    adapter_class = get_adapter_class(config.provider)

    if api_key is None:
        try:
            api_key = decrypt_api_key(config.api_key_encrypted)
        except ApiKeyDecryptError as exc:
            raise LLMError(LLMErrorType.CONFIG_ERROR, str(exc)) from exc

    return adapter_class(
        model_id=config.model_id,
        api_key=api_key,
        base_url=config.base_url,
        capabilities=normalize_capabilities(config.capabilities),
        extra_config=parse_extra_config(config.extra_config),
    )


def assert_config_usable(config: ModelConfig) -> None:
    """调用前的状态校验（启用 + 已注册供应商 + 能力非空）。"""
    if not config.is_enabled:
        raise LLMError(
            LLMErrorType.CONFIG_ERROR,
            f"模型「{config.display_name}」已被禁用",
            detail="请在「系统设置 → 模型管理」中启用它，或切换其他模型。",
        )
    if not normalize_capabilities(config.capabilities):
        raise LLMError(
            LLMErrorType.CONFIG_ERROR,
            f"模型「{config.display_name}」未配置能力标签",
            detail="请在模型配置里至少勾选一项能力，否则无法参与对话。",
        )


async def get_llm_client(
    db: AsyncSession,
    model_config_id: int,
    *,
    use_cache: bool = True,
    require_enabled: bool = True,
) -> LLMAdapter:
    """按模型配置 id 取适配器实例（工厂入口）。"""
    config = await db.get(ModelConfig, model_config_id)
    if config is None:
        raise LLMError(
            LLMErrorType.CONFIG_ERROR,
            f"模型配置不存在（id={model_config_id}）",
            detail="它可能已被删除，请重新选择一个模型。",
        )

    if require_enabled:
        assert_config_usable(config)

    try:
        api_key = decrypt_api_key(config.api_key_encrypted)
    except ApiKeyDecryptError as exc:
        raise LLMError(LLMErrorType.CONFIG_ERROR, str(exc)) from exc

    if not use_cache:
        return build_adapter(config, api_key)

    key = _cache_key(config, api_key)
    cached = _client_cache.get(key)
    if cached is not None:
        _client_cache.move_to_end(key)
        return cached

    adapter = build_adapter(config, api_key)
    _client_cache[key] = adapter
    while len(_client_cache) > _CACHE_LIMIT:
        _client_cache.popitem(last=False)
    return adapter


async def get_default_model_config(db: AsyncSession) -> ModelConfig | None:
    """取默认模型：优先 ``is_default``，否则取第一个启用的。

    返回 ``None`` 表示用户还没有配置任何可用模型，调用方应给出引导性提示。
    """
    result = await db.execute(
        select(ModelConfig)
        .where(ModelConfig.is_enabled.is_(True))
        .order_by(ModelConfig.is_default.desc(), ModelConfig.sort_order.asc(), ModelConfig.id.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def resolve_model_config(
    db: AsyncSession, model_config_id: int | None
) -> ModelConfig:
    """解析要使用的模型配置：指定了就用指定的，否则用默认。

    没有可用模型时抛出**可直接展示给用户**的错误，而不是让对话接口 500。
    """
    if model_config_id is not None:
        config = await db.get(ModelConfig, model_config_id)
        if config is None:
            raise LLMError(
                LLMErrorType.CONFIG_ERROR,
                f"模型配置不存在（id={model_config_id}）",
                detail="它可能已被删除，请重新选择一个模型。",
            )
        assert_config_usable(config)
        return config

    config = await get_default_model_config(db)
    if config is None:
        raise LLMError(
            LLMErrorType.NOT_CONFIGURED,
            "还没有可用的模型",
            detail="请到「系统设置 → 模型管理」添加一个模型并启用它。",
        )
    return config


__all__ = [
    "assert_config_usable",
    "build_adapter",
    "clear_client_cache",
    "get_default_model_config",
    "get_llm_client",
    "resolve_model_config",
]
