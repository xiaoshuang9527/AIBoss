"""模型管理路由。

安全约定（贯穿本文件）
----------------------
1. **读接口永不返回明文密钥**：响应模型 :class:`ModelConfigRead` 从类型层面
   就没有 ``api_key`` 字段，只给 ``api_key_masked`` 掩码。
2. **写接口的空值语义**：``PUT`` 时 ``api_key`` 为空 = 保持原密钥不变；
   想清空必须显式传 ``clear_api_key=true``。
3. 任何修改都会清掉适配器缓存，保证配置立即生效。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..enums import LogCategory
from ..models import ModelConfig
from ..schemas import (
    ModelConfigCreate,
    ModelConfigRead,
    ModelConfigUpdate,
    ModelProvidersResponse,
    ModelTestDraftRequest,
    ModelTestResult,
)
from ..services.llm import (
    LLMError,
    LLMErrorType,
    build_adapter,
    clear_client_cache,
    crypto,
    get_adapter_class,
    list_provider_metadata,
    normalize_capabilities,
)
from ..services.llm.capabilities import capabilities_to_json, capability_catalog_payload
from ..services.llm.factory import parse_extra_config
from ..services.llm.registry import is_registered
from ..services.logging_service import log_event
from ..utils import utcnow
from .helpers import ok

router = APIRouter(prefix="/models", tags=["模型管理"])


# ============================================================
# 转换
# ============================================================
def _provider_label(provider: str) -> str:
    if not is_registered(provider):
        return provider
    return get_adapter_class(provider).display_name or provider


def to_read_model(config: ModelConfig) -> ModelConfigRead:
    """ORM → 读模型（密钥只出掩码）。"""
    data = ModelConfigRead.model_validate(config)
    data.provider_label = _provider_label(config.provider)
    data.has_api_key = bool(config.api_key_encrypted)
    data.api_key_masked = crypto.make_hint(config.api_key_encrypted)
    data.capabilities = normalize_capabilities(config.capabilities)
    data.extra_config = parse_extra_config(config.extra_config)
    return data


async def _get_or_404(db: AsyncSession, model_id: int) -> ModelConfig:
    config = await db.get(ModelConfig, model_id)
    if config is None:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    return config


async def _clear_other_defaults(db: AsyncSession, keep_id: int | None) -> None:
    """保证同一时刻只有一个默认模型。"""
    rows = (
        await db.execute(select(ModelConfig).where(ModelConfig.is_default.is_(True)))
    ).scalars().all()
    for row in rows:
        if keep_id is None or row.id != keep_id:
            row.is_default = False


def _validate_provider(provider: str) -> None:
    if not is_registered(provider):
        raise HTTPException(
            status_code=422,
            detail=f"不支持的供应商：{provider}（可能尚未实现适配器）",
        )


def _ensure_required_credentials(provider: str, api_key: str | None, base_url: str | None) -> None:
    """按供应商要求校验必填项，给出明确错误而不是等调用时失败。"""
    adapter_class = get_adapter_class(provider)
    if adapter_class.requires_api_key and not api_key:
        raise HTTPException(
            status_code=422,
            detail=f"{adapter_class.display_name} 需要 API Key，请填写后再保存",
        )
    if not base_url and not adapter_class.default_base_url:
        raise HTTPException(
            status_code=422,
            detail=f"{adapter_class.display_name} 没有默认 API 地址，请手动填写 Base URL",
        )


# ============================================================
# 供应商元数据
# ============================================================
@router.get("/providers")
async def list_providers():
    """供应商与能力的元数据 —— 前端联动配置的唯一数据源。

    前端不硬编码任何供应商或能力清单，因此新增供应商只改后端注册表即可。
    """
    payload = ModelProvidersResponse(
        providers=list_provider_metadata(),
        capabilities=capability_catalog_payload(),
    )
    return ok(payload.model_dump())


# ============================================================
# CRUD
# ============================================================
@router.get("")
async def list_models(
    db: AsyncSession = Depends(get_db),
    provider: str | None = None,
    enabled_only: bool = Query(False),
):
    """模型列表。"""
    statement = select(ModelConfig)
    if provider:
        statement = statement.where(ModelConfig.provider == provider)
    if enabled_only:
        statement = statement.where(ModelConfig.is_enabled.is_(True))
    statement = statement.order_by(
        ModelConfig.is_default.desc(), ModelConfig.sort_order.asc(), ModelConfig.id.asc()
    )
    rows = (await db.execute(statement)).scalars().all()
    return ok([to_read_model(row).model_dump() for row in rows])


@router.post("")
async def create_model(payload: ModelConfigCreate, db: AsyncSession = Depends(get_db)):
    """新增模型配置。"""
    provider = payload.provider.strip().lower()
    _validate_provider(provider)
    _ensure_required_credentials(provider, payload.api_key, payload.base_url)

    # 同一 provider + model_id 只允许一条，否则界面上会出现两条无法区分的记录
    existing = (
        await db.execute(
            select(ModelConfig).where(
                ModelConfig.provider == provider, ModelConfig.model_id == payload.model_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"已存在相同的模型配置（{provider} / {payload.model_id}）。"
                "如需换密钥请直接编辑该条，避免重复接入。"
            ),
        )

    config = ModelConfig(
        display_name=payload.display_name,
        provider=provider,
        model_id=payload.model_id,
        base_url=payload.base_url,
        api_key_encrypted=crypto.encrypt_api_key(payload.api_key),
        capabilities=capabilities_to_json(payload.capabilities),
        extra_config=json.dumps(payload.extra_config, ensure_ascii=False),
        is_enabled=payload.is_enabled,
        is_default=payload.is_default,
        sort_order=payload.sort_order,
        remark=payload.remark,
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)

    if config.is_default:
        await _clear_other_defaults(db, config.id)
        await db.commit()
        await db.refresh(config)

    clear_client_cache()
    await log_event(
        db,
        f"新增模型配置：{config.display_name}（{config.provider} / {config.model_id}）",
        category=LogCategory.SYSTEM,
        detail=f"capabilities={config.capabilities} enabled={config.is_enabled}",
    )
    return ok(to_read_model(config).model_dump())


@router.get("/{model_id}")
async def get_model(model_id: int, db: AsyncSession = Depends(get_db)):
    config = await _get_or_404(db, model_id)
    return ok(to_read_model(config).model_dump())


@router.put("/{model_id}")
async def update_model(
    model_id: int, payload: ModelConfigUpdate, db: AsyncSession = Depends(get_db)
):
    """更新模型配置。

    ``api_key`` 为空 = 保持原密钥不变；``clear_api_key=true`` 才清空。
    """
    config = await _get_or_404(db, model_id)
    provider = (payload.provider or config.provider).strip().lower()
    _validate_provider(provider)

    if payload.display_name is not None:
        config.display_name = payload.display_name
    config.provider = provider
    if payload.model_id is not None:
        config.model_id = payload.model_id
    if payload.base_url is not None:
        config.base_url = payload.base_url or None

    # ---- 密钥三态处理 ----
    if payload.clear_api_key:
        config.api_key_encrypted = None
    elif payload.api_key is not None:
        config.api_key_encrypted = crypto.encrypt_api_key(payload.api_key)

    if payload.capabilities is not None:
        config.capabilities = capabilities_to_json(payload.capabilities)
    if payload.extra_config is not None:
        config.extra_config = json.dumps(payload.extra_config, ensure_ascii=False)
    if payload.is_enabled is not None:
        config.is_enabled = payload.is_enabled
    if payload.sort_order is not None:
        config.sort_order = payload.sort_order
    if payload.remark is not None:
        config.remark = payload.remark or None

    # 保存前再校验一次凭据是否齐全（用户可能把 Key 清空了）
    _ensure_required_credentials(provider, config.api_key_encrypted, config.base_url)

    if payload.is_default is not None:
        config.is_default = payload.is_default
    if payload.is_default:
        await _clear_other_defaults(db, config.id)

    await db.commit()
    await db.refresh(config)
    clear_client_cache()

    await log_event(
        db,
        f"更新模型配置：{config.display_name}",
        category=LogCategory.SYSTEM,
    )
    return ok(to_read_model(config).model_dump())


@router.delete("/{model_id}")
async def delete_model(model_id: int, db: AsyncSession = Depends(get_db)):
    config = await _get_or_404(db, model_id)
    name = config.display_name
    await db.delete(config)
    await db.commit()
    clear_client_cache()
    await log_event(db, f"删除模型配置：{name}", category=LogCategory.SYSTEM)
    return ok({"deleted": True})


# ============================================================
# 状态操作
# ============================================================
@router.patch("/{model_id}/enabled")
async def set_enabled(
    model_id: int, enabled: bool = Query(...), db: AsyncSession = Depends(get_db)
):
    config = await _get_or_404(db, model_id)
    config.is_enabled = enabled
    # 禁用的模型不应继续占着默认位
    if not enabled and config.is_default:
        config.is_default = False
    await db.commit()
    await db.refresh(config)
    clear_client_cache()
    return ok(to_read_model(config).model_dump())


@router.patch("/{model_id}/default")
async def set_default(model_id: int, db: AsyncSession = Depends(get_db)):
    config = await _get_or_404(db, model_id)
    if not config.is_enabled:
        raise HTTPException(status_code=422, detail="被禁用的模型不能设为默认，请先启用它")
    config.is_default = True
    await _clear_other_defaults(db, config.id)
    await db.commit()
    await db.refresh(config)
    return ok(to_read_model(config).model_dump())


@router.post("/{model_id}/clone")
async def clone_model(
    model_id: int, db: AsyncSession = Depends(get_db), name: str | None = None
):
    """克隆一份配置。**密钥一并复制**，便于接入同供应商下的另一个模型。"""
    source = await _get_or_404(db, model_id)
    clone = ModelConfig(
        display_name=(name or f"{source.display_name} 副本")[:128],
        provider=source.provider,
        # 允许同模型克隆会撞唯一约束，因此强制要求改名后模型 ID 也不同
        model_id=source.model_id,
        base_url=source.base_url,
        api_key_encrypted=source.api_key_encrypted,
        capabilities=source.capabilities,
        extra_config=source.extra_config,
        is_enabled=False,  # 克隆体默认禁用，避免界面上突然多出一个可用模型
        is_default=False,
        sort_order=source.sort_order,
        remark=source.remark,
    )
    # 唯一约束要求 (provider, model_id) 不同，故给克隆体加后缀占位
    clone.model_id = f"{source.model_id}-copy"
    db.add(clone)
    await db.commit()
    await db.refresh(clone)
    clear_client_cache()
    await log_event(
        db, f"克隆模型配置：{source.display_name} → {clone.display_name}", category=LogCategory.SYSTEM
    )
    return ok(to_read_model(clone).model_dump())


# ============================================================
# 测试连接
# ============================================================
def _test_result_payload(result) -> dict:
    return ModelTestResult(
        ok=result.ok,
        latency_ms=result.latency_ms,
        model_echo=result.model_echo,
        message=result.message,
        error_type=result.error_type,
        suggestion=result.suggestion,
        detail=result.detail,
    ).model_dump()


def _record_test_outcome(config: ModelConfig, payload: dict) -> None:
    """把测试结果写回配置，列表页可直接展示。"""
    config.last_tested_at = utcnow()
    config.last_test_ok = bool(payload.get("ok"))
    config.last_test_latency_ms = int(payload.get("latency_ms") or 0)
    message = payload.get("message") or ""
    suggestion = payload.get("suggestion") or ""
    config.last_test_message = f"{message}｜{suggestion}" if suggestion else message


@router.post("/{model_id}/test")
async def test_model(model_id: int, db: AsyncSession = Depends(get_db)):
    """测试已保存模型的连通性。"""
    config = await _get_or_404(db, model_id)
    try:
        adapter = build_adapter(config)
    except LLMError as exc:
        # 配置本身有问题（例如密钥解不开），也算测试结果，不抛 500
        payload = ModelTestResult(
            ok=False,
            message=exc.title,
            error_type=exc.error_type.value,
            suggestion=exc.suggestion,
            detail=exc.detail,
        ).model_dump()
        _record_test_outcome(config, payload)
        await db.commit()
        return ok(payload)

    result = await adapter.test_connection()
    payload = _test_result_payload(result)
    _record_test_outcome(config, payload)
    await db.commit()

    await log_event(
        db,
        f"测试模型连接：{config.display_name} → {'成功' if result.ok else '失败'}",
        detail=payload.get("detail") or payload.get("message"),
        category=LogCategory.SYSTEM,
    )
    return ok(payload)


@router.post("/test-draft")
async def test_draft(payload: ModelTestDraftRequest, db: AsyncSession = Depends(get_db)):
    """测试**尚未保存**的配置（弹窗里点「测试连接」时还没有 id）。

    编辑已有模型但没改密钥时，传 ``model_config_id`` 复用库里已存的密钥，
    这样用户不必为了测试而重新粘贴一次 Key。
    """
    _validate_provider(payload.provider)

    api_key = payload.api_key
    if not api_key and payload.model_config_id:
        existing = await db.get(ModelConfig, payload.model_config_id)
        if existing is not None:
            try:
                api_key = crypto.decrypt_api_key(existing.api_key_encrypted)
            except crypto.ApiKeyDecryptError as exc:
                return ok(
                    ModelTestResult(
                        ok=False,
                        message="API Key 解密失败",
                        error_type=LLMErrorType.CONFIG_ERROR.value,
                        detail=str(exc),
                    ).model_dump()
                )

    # 用临时 ORM 对象承载草稿（不落库）
    draft = ModelConfig(
        display_name=payload.model_id,
        provider=payload.provider.strip().lower(),
        model_id=payload.model_id,
        base_url=payload.base_url,
        api_key_encrypted=crypto.encrypt_api_key(api_key),
        capabilities=capabilities_to_json(payload.capabilities),
        extra_config=json.dumps(payload.extra_config, ensure_ascii=False),
        is_enabled=True,
    )

    try:
        adapter = build_adapter(draft)
        result = await adapter.test_connection()
    except LLMError as exc:
        return ok(
            ModelTestResult(
                ok=False,
                message=exc.title,
                error_type=exc.error_type.value,
                suggestion=exc.suggestion,
                detail=exc.detail,
            ).model_dump()
        )

    return ok(_test_result_payload(result))
