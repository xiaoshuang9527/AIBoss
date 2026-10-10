"""Pydantic 请求 / 响应模型（Pydantic v2）。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class QueueStatus(BaseModel):
    running: int = 0
    pending: int = 0
    max_concurrent: int = 0
    slots_free: int = 0
    worker_alive: bool = False
    running_task_ids: list[int] = Field(default_factory=list)
