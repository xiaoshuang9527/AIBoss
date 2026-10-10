"""ORM 模型。

设计要点：

* ``AuthProfile`` 保存 BOSS 直聘登录态。真正的凭据（Cookie / Local State 快照）
  以密文形式落盘，数据库里只存路径与指纹，避免数据库泄露直接等于账号泄露。
* ``ScrapeTask`` 承载一次抓取的全部输入（关键词 / 城市 / 薪资 / 经验 / 页数上限）
  与执行元信息（状态、耗时、错误、重试次数）。
* ``JobRecord`` 是岗位明细，按 ``job_id``（爬虫侧 md5 指纹）全局去重，
  因此同一岗位被多个任务命中时不会重复计数。
* ``SystemLog`` 记录操作审计与运行日志，可按分类/级别过滤。

再次强调：本工具是纯个人求职辅助，**不存在任何额度、计费、套餐字段**。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .enums import AuthStatus, LogCategory, LogLevel, TaskStatus


def _now_column() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AuthProfile(Base):
    """BOSS 直聘登录态档案（对应一个隔离的 Chrome profile）。"""

    __tablename__ = "auth_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="默认账号")

    # Chrome ``--user-data-dir`` 路径（隔离 profile，不碰用户主浏览器）
    profile_dir: Mapped[str] = mapped_column(String(512), nullable=False)
    # 档案指纹：用于目录命名与前端展示，不含明文凭据
    profile_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    browser: Mapped[str] = mapped_column(String(16), nullable=False, default="chrome")
    cdp_port: Mapped[int] = mapped_column(Integer, nullable=False, default=9222)

    # 登录态加密快照（Cookie + Local State），文件路径而非明文
    cookie_blob_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    cookie_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 账号展示信息（脱敏），例如手机号掩码、最近登录时间
    account_label: Mapped[str | None] = mapped_column(String(128), nullable=True)

    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=AuthStatus.UNKNOWN.value, index=True
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_hint: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _now_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    tasks: Mapped[list["ScrapeTask"]] = relationship(back_populates="auth_profile")

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<AuthProfile id={self.id} name={self.name!r} status={self.status}>"


class ScrapeTask(Base):
    """一次抓取任务。"""

    __tablename__ = "scrape_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ---------- 用户输入 ----------
    keyword: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    city: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    salary: Mapped[str | None] = mapped_column(String(64), nullable=True)
    experience: Mapped[str | None] = mapped_column(String(64), nullable=True)
    degree: Mapped[str | None] = mapped_column(String(64), nullable=True)
    company_scale: Mapped[str | None] = mapped_column(String(64), nullable=True)
    company_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(64), nullable=True)
    max_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    with_detail: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ---------- 执行状态 ----------
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=TaskStatus.PENDING.value, index=True
    )
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0-100
    stage_text: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # ---------- 结果统计 ----------
    total_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ---------- 运行元信息 ----------
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    result_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    auth_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("auth_profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    auth_profile: Mapped[AuthProfile | None] = relationship(back_populates="tasks")

    created_at: Mapped[datetime] = _now_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    jobs: Mapped[list["JobRecord"]] = relationship(
        back_populates="task",
        foreign_keys="JobRecord.first_task_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_scrape_tasks_status_created", "status", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ScrapeTask id={self.id} {self.keyword!r}@{self.city} status={self.status}>"


class JobRecord(Base):
    """岗位明细。

    ``job_id`` 是爬虫根据岗位链接生成的 16 位 md5 指纹，全局唯一，
    所以不同任务抓到同一个岗位时只保留一条记录，``first_task_id`` 记住首次发现者。
    """

    __tablename__ = "job_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    # ---------- 列表字段 ----------
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="", index=True)
    company: Mapped[str] = mapped_column(String(255), nullable=False, default="", index=True)
    salary: Mapped[str | None] = mapped_column(String(64), nullable=True)
    salary_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 单位：K/月
    salary_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_avg: Mapped[int | None] = mapped_column(Integer, nullable=True)

    city: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    experience: Mapped[str | None] = mapped_column(String(64), nullable=True)
    degree: Mapped[str | None] = mapped_column(String(64), nullable=True)

    company_scale: Mapped[str | None] = mapped_column(String(64), nullable=True)
    company_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    company_industry: Mapped[str | None] = mapped_column(String(128), nullable=True)

    boss_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    boss_title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    boss_active_status: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ---------- 技能标签 ----------
    # 爬虫列表接口给的 skills / job_labels（``|`` 分隔的原始串）
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    skills_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    welfare: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 详情页提取的技能标签（JSON 数组字符串）
    skill_tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 归一化后的高频技能词，便于前端直接渲染与筛选（JSON 数组字符串）
    skill_keywords: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---------- 岗位详情全文 ----------
    jd: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_duty: Mapped[str | None] = mapped_column(Text, nullable=True)      # 岗位职责
    job_requirement: Mapped[str | None] = mapped_column(Text, nullable=True)  # 岗位要求

    # ---------- 跳转链接 ----------
    job_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    company_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    security_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lid: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # ---------- 归属与时间 ----------
    first_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("scrape_tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    last_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("scrape_tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    task: Mapped[ScrapeTask | None] = relationship(
        back_populates="jobs", foreign_keys=[first_task_id]
    )

    is_new: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    detail_fetched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)  # 原始 JSON，便于回溯

    scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _now_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_job_records_city_salary", "city", "salary_avg"),
        Index("ix_job_records_created", "created_at"),
        UniqueConstraint("job_id", name="uq_job_records_job_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<JobRecord id={self.id} {self.title!r}@{self.company}>"


class SystemLog(Base):
    """系统操作日志（审计 + 运行日志）。"""

    __tablename__ = "system_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(
        String(16), nullable=False, default=LogLevel.INFO.value, index=True
    )
    category: Mapped[str] = mapped_column(
        String(32), nullable=False, default=LogCategory.SYSTEM.value, index=True
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    auth_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = _now_column()

    __table_args__ = (Index("ix_system_logs_created", "created_at"),)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SystemLog {self.level} {self.category} {self.message[:40]!r}>"


class AppSetting(Base):
    """键值型运行时设置（系统设置页保存的偏好）。"""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
