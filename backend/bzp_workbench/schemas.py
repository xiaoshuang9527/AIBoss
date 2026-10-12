"""Pydantic 请求 / 响应模型（Pydantic v2）。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import settings

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ApiResponse(BaseModel, Generic[T]):
    """统一响应信封。"""

    code: int = 0
    message: str = "ok"
    data: T | None = None


class PageMeta(BaseModel):
    total: int = 0
    page: int = 1
    page_size: int = 20
    pages: int = 0


class PageData(BaseModel, Generic[T]):
    items: list[T] = Field(default_factory=list)
    meta: PageMeta = Field(default_factory=PageMeta)


# ============================================================
# 授权档案
# ============================================================
class AuthProfileRead(ORMModel):
    id: int
    name: str
    browser: str
    cdp_port: int
    status: str
    status_label: str = ""
    account_label: str | None = None
    last_verified_at: datetime | None = None
    last_error: str | None = None
    is_default: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_profile(cls, profile) -> "AuthProfileRead":
        from .enums import AuthStatus

        data = cls.model_validate(profile)
        try:
            data.status_label = AuthStatus(profile.status).label
        except ValueError:
            data.status_label = profile.status
        return data


class AuthStartRequest(BaseModel):
    """启动授权（扫码登录）。"""

    browser: Literal["chrome", "edge"] = "chrome"
    copy_login_state: bool = Field(
        default=False,
        description="是否从本机主浏览器导入 Cookie（仅在用户明确知情时使用）",
    )
    reset_profile: bool = Field(default=False, description="是否先重建隔离 profile")
    name: str = Field(default="默认账号", max_length=128)
    external_browser: bool = Field(
        default=False,
        description=(
            "不尝试自动启动浏览器，直接接管一个已开启 CDP 调试端口的实例。"
            "适用于服务端无法拉起 Chrome 的场景（无图形界面 / 受限运行环境）"
        ),
    )


class AuthSessionRead(BaseModel):
    """授权会话的实时状态。"""

    session_id: str
    profile_id: int
    browser: str
    cdp_port: int
    phase: Literal["starting", "awaiting_login", "verifying", "success", "failed", "cancelled"]
    phase_label: str = ""
    message: str = ""
    login_url: str | None = None
    qr_hint: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    log_tail: list[str] = Field(default_factory=list)


class AuthVerifyResult(BaseModel):
    ok: bool
    status: str
    status_label: str = ""
    message: str = ""
    detail: str | None = None


# ============================================================
# 抓取任务
# ============================================================
class TaskCreate(BaseModel):
    """创建抓取任务。"""

    keyword: str = Field(min_length=1, max_length=128, description="求职方向/关键词")
    city: str = Field(min_length=1, max_length=64, description="目标城市")
    salary: str | None = Field(default=None, max_length=64, description="薪资要求，如 15k-25k")
    experience: str | None = Field(default=None, max_length=64)
    degree: str | None = Field(default=None, max_length=64)
    company_scale: str | None = Field(default=None, max_length=64)
    company_stage: str | None = Field(default=None, max_length=64)
    industry: str | None = Field(default=None, max_length=64)
    max_pages: int = Field(default=3, ge=1, description="抓取页数上限")
    with_detail: bool = Field(default=True, description="是否抓取岗位详情 JD")
    note: str | None = Field(default=None, max_length=255)
    auth_profile_id: int | None = None

    @field_validator("keyword", "city")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("不能为空")
        return cleaned

    @field_validator("max_pages")
    @classmethod
    def _cap_pages(cls, value: int) -> int:
        # 硬上限：防止把页数填成 50 直接触发风控
        return max(1, min(int(value), settings.max_pages_per_task))

    @field_validator(
        "salary", "experience", "degree", "company_scale", "company_stage", "industry", "note"
    )
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class TaskRead(ORMModel):
    id: int
    keyword: str
    city: str
    salary: str | None = None
    experience: str | None = None
    degree: str | None = None
    company_scale: str | None = None
    company_stage: str | None = None
    industry: str | None = None
    max_pages: int
    with_detail: bool
    note: str | None = None

    status: str
    status_label: str = ""
    progress: int = 0
    stage_text: str | None = None

    total_found: int = 0
    new_count: int = 0
    detail_count: int = 0

    attempts: int = 0
    error_message: str | None = None
    duration_seconds: float | None = None

    auth_profile_id: int | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @classmethod
    def from_orm_task(cls, task) -> "TaskRead":
        from .enums import TaskStatus

        data = cls.model_validate(task)
        try:
            data.status_label = TaskStatus(task.status).label
        except ValueError:
            data.status_label = task.status
        return data


class TaskLogRead(BaseModel):
    """任务实时日志（读取运行日志文件的尾部）。"""

    task_id: int
    status: str
    progress: int
    stage_text: str | None = None
    lines: list[str] = Field(default_factory=list)
    total_lines: int = 0


# ============================================================
# 岗位
# ============================================================
class JobRead(ORMModel):
    id: int
    job_id: str
    title: str
    company: str
    salary: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_avg: int | None = None
    city: str | None = None
    location: str | None = None
    experience: str | None = None
    degree: str | None = None
    company_scale: str | None = None
    company_stage: str | None = None
    company_industry: str | None = None
    boss_name: str | None = None
    boss_title: str | None = None
    boss_active_status: str | None = None
    tags: str | None = None
    welfare: str | None = None
    skill_tags: list[str] = Field(default_factory=list)
    skill_keywords: list[str] = Field(default_factory=list)
    jd: str | None = None
    job_duty: str | None = None
    job_requirement: str | None = None
    job_link: str | None = None
    company_link: str | None = None
    is_new: bool = True
    is_favorite: bool = False
    detail_fetched: bool = False
    first_task_id: int | None = None
    scraped_at: datetime | None = None
    created_at: datetime

    @field_validator("skill_tags", "skill_keywords", mode="before")
    @classmethod
    def _parse_json_list(cls, value: Any) -> list[str]:
        """数据库里存的是 JSON 数组字符串，对外统一输出真正的数组。"""
        if value is None:
            return []
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                return []
        if isinstance(value, (list, tuple, set)):
            return [str(item) for item in value if str(item).strip()]
        return []

    @classmethod
    def from_orm_job(cls, job) -> "JobRead":
        return cls.model_validate(job)


class JobListItem(JobRead):
    """列表页用的精简版本（不含 JD 全文，减小响应体积）。"""


class JobFacet(BaseModel):
    value: str
    count: int


class JobFacets(BaseModel):
    cities: list[JobFacet] = Field(default_factory=list)
    experiences: list[JobFacet] = Field(default_factory=list)
    degrees: list[JobFacet] = Field(default_factory=list)
    skills: list[JobFacet] = Field(default_factory=list)
    salary_min: int | None = None
    salary_max: int | None = None


# ============================================================
# 概览 / 图表
# ============================================================
class OverviewCards(BaseModel):
    total_jobs: int = 0
    today_new_jobs: int = 0
    active_tasks: int = 0
    auth_status: str = "unknown"
    auth_status_label: str = "未校验"
    auth_account: str | None = None
    auth_verified_at: datetime | None = None
    # 辅助指标（非计费，仅统计用途）
    total_tasks: int = 0
    succeeded_tasks: int = 0
    failed_tasks: int = 0
    today_tasks: int = 0
    favorite_jobs: int = 0


class ChartSeries(BaseModel):
    name: str
    data: list[Any] = Field(default_factory=list)


class ChartData(BaseModel):
    categories: list[str] = Field(default_factory=list)
    series: list[ChartSeries] = Field(default_factory=list)


class TopSkill(BaseModel):
    skill: str
    count: int


class DashboardSummary(BaseModel):
    cards: OverviewCards
    status_text: str = "服务正常"
    status_level: Literal["ok", "warning", "error"] = "ok"
    salary_distribution: ChartData = Field(default_factory=ChartData)
    experience_distribution: ChartData = Field(default_factory=ChartData)
    city_distribution: ChartData = Field(default_factory=ChartData)
    daily_trend: ChartData = Field(default_factory=ChartData)
    top_skills: list[TopSkill] = Field(default_factory=list)
    top_companies: list[JobFacet] = Field(default_factory=list)
    recent_tasks: list[TaskRead] = Field(default_factory=list)
    disclaimer: str = ""


# ============================================================
# 导出
# ============================================================
class ExportRequest(BaseModel):
    task_id: int | None = None
    keyword: str | None = None
    city: str | None = None
    format: Literal["csv", "json"] = "csv"
    include_jd: bool = True


# ============================================================
# 设置与元数据
# ============================================================
class ScraperHealth(BaseModel):
    scraper_dir: str
    entry_exists: bool
    python_executable: str
    version: str | None = None
    cdp_port: int
    cdp_reachable: bool = False
    profile_dir: str | None = None
    profile_exists: bool = False
    # CDP 端口通 ≠ 已登录，这两个状态要分开暴露给前端
    authorized: bool = False
    message: str = ""


class SystemSettingsRead(BaseModel):
    app_name: str
    app_version: str
    max_concurrent_tasks: int
    task_delay_min: float
    task_delay_max: float
    max_retries: int
    max_pages_per_task: int
    task_timeout_seconds: int
    auth_timeout_seconds: int
    request_budget: int
    cdp_port: int
    database_dialect: str
    storage_root: str
    disclaimer: str


class SystemSettingsUpdate(BaseModel):
    max_concurrent_tasks: int | None = None
    task_delay_min: float | None = None
    task_delay_max: float | None = None
    max_retries: int | None = None
    max_pages_per_task: int | None = None
    task_timeout_seconds: int | None = None
    request_budget: int | None = None


class LogRead(ORMModel):
    id: int
    level: str
    category: str
    message: str
    detail: str | None = None
    task_id: int | None = None
    created_at: datetime


# ============================================================
# 模型管理
# ============================================================
class ModelConfigRead(ORMModel):
    """模型配置的读模型。

    ⚠️ **刻意不定义 ``api_key`` 字段**：从类型系统上杜绝明文密钥泄漏，
    而不是依赖开发者记得手动过滤。外部只能拿到 ``api_key_masked``。
    """

    id: int
    display_name: str
    provider: str
    provider_label: str = ""
    model_id: str
    base_url: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    extra_config: dict[str, Any] = Field(default_factory=dict)

    is_enabled: bool = True
    is_default: bool = False
    sort_order: int = 0
    remark: str | None = None

    #: 密钥是否已配置（不暴露内容）
    has_api_key: bool = False
    #: 掩码，如 ``sk-****abcd``
    api_key_masked: str | None = None

    last_tested_at: datetime | None = None
    last_test_ok: bool | None = None
    last_test_message: str | None = None
    last_test_latency_ms: int | None = None

    created_at: datetime
    updated_at: datetime

    @field_validator("capabilities", mode="before")
    @classmethod
    def _parse_capabilities(cls, value: Any) -> list[str]:
        from .services.llm.capabilities import normalize_capabilities

        return normalize_capabilities(value)

    @field_validator("extra_config", mode="before")
    @classmethod
    def _parse_extra(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (ValueError, TypeError):
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return value if isinstance(value, dict) else {}


class ModelConfigCreate(BaseModel):
    """新增模型配置。"""

    display_name: str = Field(min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=32)
    model_id: str = Field(min_length=1, max_length=128)
    base_url: str | None = Field(default=None, max_length=512)
    api_key: str | None = Field(default=None, description="明文只在写入时出现，不会回传")
    capabilities: list[str] = Field(default_factory=list)
    extra_config: dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool = True
    is_default: bool = False
    sort_order: int = 0
    remark: str | None = Field(default=None, max_length=255)

    @field_validator("display_name", "provider", "model_id")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("不能为空")
        return cleaned

    @field_validator("base_url", "api_key", "remark")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ModelConfigUpdate(BaseModel):
    """更新模型配置。

    **空值语义（重要）**：``api_key`` 为空表示「保持原密钥不变」。
    否则用户只想改个显示名，就会把已保存的 Key 抹掉。
    若要显式清空密钥，传 ``clear_api_key=true``。
    """

    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    provider: str | None = Field(default=None, min_length=1, max_length=32)
    model_id: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, max_length=512)
    api_key: str | None = None
    clear_api_key: bool = False
    capabilities: list[str] | None = None
    extra_config: dict[str, Any] | None = None
    is_enabled: bool | None = None
    is_default: bool | None = None
    sort_order: int | None = None
    remark: str | None = Field(default=None, max_length=255)


class ModelTestDraftRequest(BaseModel):
    """未保存的配置也能测试连接（弹窗里点「测试连接」时还没有 id）。"""

    provider: str
    model_id: str = Field(min_length=1)
    base_url: str | None = None
    api_key: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    extra_config: dict[str, Any] = Field(default_factory=dict)
    #: 编辑已有模型但未改 Key 时，用它复用数据库里已存的密钥
    model_config_id: int | None = None

    @field_validator("api_key")
    @classmethod
    def _blank_key_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ModelTestResult(BaseModel):
    """测试连接结果。"""

    ok: bool
    latency_ms: int = 0
    model_echo: str | None = None
    message: str = ""
    error_type: str | None = None
    suggestion: str | None = None
    detail: str | None = None


class CapabilityOption(BaseModel):
    value: str
    label: str
    description: str = ""


class ProviderExtraField(BaseModel):
    key: str
    label: str
    type: str = "text"
    placeholder: str | None = None
    description: str | None = None


class ProviderMetadata(BaseModel):
    """供应商元数据 —— 前端下拉、能力联动、默认地址的唯一数据源。"""

    provider: str
    display_name: str
    default_base_url: str | None = None
    requires_api_key: bool = True
    base_url_editable: bool = True
    supported_capabilities: list[str] = Field(default_factory=list)
    doc_url: str | None = None
    suggested_models: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    extra_fields: list[ProviderExtraField] = Field(default_factory=list)


class ModelProvidersResponse(BaseModel):
    """``/api/models/providers`` 的响应。"""

    providers: list[ProviderMetadata] = Field(default_factory=list)
    capabilities: list[CapabilityOption] = Field(default_factory=list)


# ============================================================
# AI 对话
# ============================================================
class ChatConversationRead(ORMModel):
    id: int
    title: str
    mode: str
    model_config_id: int | None = None
    message_count: int = 0
    total_tokens: int = 0
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None


class ChatMessageRead(ORMModel):
    """一条对话消息。

    ``content_json`` 与 ``tool_calls_json`` 在库中是字符串，这里解析成对象，
    前端可以直接按 ``content_type`` 渲染结构化载荷（岗位卡片 / 诊断报告等）。
    """

    id: int
    conversation_id: int
    role: str
    content: str | None = None
    content_type: str = "text"
    content_data: Any = None
    reasoning: str | None = None
    attachments: list[int] = Field(default_factory=list)
    tool_calls: list[Any] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: int | None = None
    error_type: str | None = None
    error_message: str | None = None
    model_config_id: int | None = None
    model_label: str | None = None
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _unpack_json_columns(cls, value: Any) -> Any:
        """把 ORM 对象上的 JSON 字符串列拆成结构化字段。

        放在 schema 层做而不是在每个路由里重复处理，避免遗漏。
        """
        if isinstance(value, dict):
            return value

        raw_content_json = getattr(value, "content_json", None)
        raw_attachments = getattr(value, "attachments_json", None)
        raw_tools = getattr(value, "tool_calls_json", None)

        def _loads(raw, default):
            if not raw:
                return default
            try:
                parsed = json.loads(raw)
            except (ValueError, TypeError):
                return default
            return parsed

        return {
            "id": value.id,
            "conversation_id": value.conversation_id,
            "role": value.role,
            "content": value.content,
            "content_type": value.content_type,
            "content_data": _loads(raw_content_json, None),
            "reasoning": value.reasoning,
            "attachments": _loads(raw_attachments, []),
            "tool_calls": _loads(raw_tools, []),
            "prompt_tokens": value.prompt_tokens,
            "completion_tokens": value.completion_tokens,
            "cached_tokens": value.cached_tokens,
            "latency_ms": value.latency_ms,
            "error_type": value.error_type,
            "error_message": value.error_message,
            "model_config_id": value.model_config_id,
            "model_label": value.model_label,
            "created_at": value.created_at,
        }


class ChatAttachmentRead(ORMModel):
    """附件元数据。

    ``extracted_text`` 默认不返回（可能在几万字符量级），
    需要时通过 ``/api/chat/attachments/{id}`` 单独取。
    """

    id: int
    conversation_id: int
    message_id: int | None = None
    kind: str
    filename: str
    mime_type: str | None = None
    size_bytes: int = 0
    size_label: str = ""
    extract_status: str = "pending"
    extract_error: str | None = None
    extracted_text: str | None = None
    created_at: datetime


class ChatSendRequest(BaseModel):
    """发送消息。"""

    content: str = Field(default="", max_length=20000)
    attachment_ids: list[int] = Field(default_factory=list)
    #: 本次发送临时指定模型（不传则沿用会话设置或全局默认）
    model_config_id: int | None = None
    #: 显式指定意图模式。传了就跳过自动路由 —— 用于用户手动纠正模式判断。
    #: 留空（None）表示走自动路由；传 "auto" 表示清空模式重新自动判断。
    mode: str | None = None


class ChatModeUpdate(BaseModel):
    """手动切换会话的意图模式。"""

    mode: str = Field(description="job_hunting / interview_coach / chat / auto")
    #: 是否清空槽位台账（换话题时应该清，否则会带着上一个话题的条件）
    reset_slots: bool = True


class ChatConfigRead(BaseModel):
    max_image_bytes: int
    max_doc_bytes: int
    max_attachments: int
    fake_llm: bool
    max_image_label: str
    max_doc_label: str
    accepted: str


class QueueStatus(BaseModel):
    running: int = 0
    pending: int = 0
    max_concurrent: int = 0
    slots_free: int = 0
    worker_alive: bool = False
    running_task_ids: list[int] = Field(default_factory=list)
