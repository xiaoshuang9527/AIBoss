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

    #: 由哪个 AI 对话发起的（为空表示从「创建抓取任务」页发起）。
    #:
    #: 用途：把任务进度推回发起它的那个对话。没有这个字段就无法回答
    #: 「这条进度该发给哪个会话」，只能全局广播 —— 用户的多个对话会同时
    #: 收到不相干的任务消息。
    #:
    #: 刻意不加 ForeignKey：对话可以被单独删除，而任务是有价值的抓取记录，
    #: 不应该因为对话被删而受牵连（ON DELETE SET NULL 也行，但对话删除走的是
    #: 应用层逻辑，加外键反而会让删除顺序变成隐式约束）。
    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

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


class ModelConfig(Base):
    """大模型接入配置（模型管理模块）。

    设计要点
    --------
    * **密钥只存密文**：``api_key_encrypted`` 用独立子密钥加密（见
      ``services/llm/crypto.py``），读接口一律不回传明文，只回掩码。
      掩码由密文实时推导，不单独存列——避免两处状态不一致。
    * **供应商差异靠元数据**：``provider`` 对应适配器注册表里的 key；
      ``extra_config`` 用 JSON 承载供应商特有参数，这样新增供应商
      **不需要改表结构**。
    * **能力标签**：``capabilities`` 是 JSON 数组，取值见
      ``services/llm/capabilities.py``。发请求前用它做前置校验，
      避免把「不支持的能力」送到 API 才报错。
    * 同一 ``provider`` + ``model_id`` 只允许配置一条，避免重复接入
      同一个模型导致界面上出现无法区分的两条记录。
    """

    __tablename__ = "model_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ---------- 展示与路由 ----------
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # ---------- 凭据（仅密文） ----------
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---------- 能力与供应商特有参数 ----------
    capabilities: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    extra_config: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---------- 状态 ----------
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ---------- 最近一次连通性测试结果（列表页直接展示，不必重新探测） ----------
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_test_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ---------- 备注 ----------
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = _now_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("provider", "model_id", name="uq_model_configs_provider_model"),
        Index("ix_model_configs_enabled_sort", "is_enabled", "sort_order"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ModelConfig id={self.id} {self.display_name!r} "
            f"provider={self.provider} model={self.model_id}>"
        )


# ============================================================
# AI 对话模块
# ============================================================


class ChatConversation(Base):
    """一个对话会话。

    设计要点
    --------
    * ``mode`` 记录当前意图模式（``job_hunting`` / ``interview_coach`` /
      ``chat``），由 AI 自动识别、用户可手动覆盖。
    * ``slot_state`` 是**服务端维护的槽位台账**（JSON）。多轮对话里模型经常
      "忘记"已经收集过哪些条件，把进度放在服务端并每轮注入 prompt，
      比指望模型自己记住可靠得多，也能显著减少重复提问。
    * ``model_config_id`` 记录本会话选用的模型，为空时回退到全局默认模型。
    """

    __tablename__ = "chat_conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False, default="新对话")
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="chat", index=True)

    #: 槽位台账：{"keyword": "Python", "city": "深圳", ...}
    slot_state: Mapped[str | None] = mapped_column(Text, nullable=True)

    model_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_configs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 累计 token，便于观察成本（不是计费功能，仅供自用监控）
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = _now_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (Index("ix_chat_conversations_updated", "updated_at"),)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ChatConversation id={self.id} {self.title!r} mode={self.mode}>"


class ChatMessage(Base):
    """一条对话消息。

    ``content_json`` 承载**结构化载荷**，前端按 ``content_type`` 分别渲染：

    * ``text``            —— 普通文本（``content`` 里是 markdown）
    * ``job_cards``       —— 岗位卡片列表（供「去 BOSS 沟通」按钮使用）
    * ``match_report``    —— 匹配度诊断报告
    * ``interview_set``   —— STAR 面试题集合
    * ``system_notice``   —— 系统通知（如抓取完成回推）

    这样设计的原因：AI 的输出不总是纯文本，工具结果需要专门的 UI，
    如果全部塞进 markdown，前端就没法渲染出可点击的岗位卡片。
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: system / user / assistant / tool
    role: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)

    content_type: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    content_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: 推理模型的思考过程（DeepSeek 的 reasoning_content）
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: 引用到的附件 id 列表（JSON）
    attachments_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 工具调用记录（JSON 数组），便于回溯 AI 做过什么
    tool_calls_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: 本消息消耗的 token（prompt 存一次即可，避免重复计）
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 输出里属于「思考过程」的部分（推理模型）。
    #: 单独存才能看出"token 花在思考还是正文上"—— 实测推理模型的思考
    #: 常占输出的绝大部分，这是成本看板最有价值的一列。
    reasoning_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: 失败时的错误分类与提示（复用 llm/errors.py 的分类）
    error_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: 生成该消息所用的模型（便于对比不同模型的表现）
    model_config_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_label: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = _now_column()

    conversation: Mapped[ChatConversation] = relationship(back_populates="messages")
    attachments: Mapped[list["ChatAttachment"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (Index("ix_chat_messages_conv_created", "conversation_id", "created_at"),)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ChatMessage id={self.id} role={self.role} type={self.content_type}>"


class ChatAttachment(Base):
    """上传的附件（简历截图 / PDF / Word）。

    ``extracted_text`` 缓存抽取结果 —— 这一步是**必需**而非优化：
    DeepSeek 接口规定图片只能出现在 ``user`` 消息里，若每轮都把原图重新
    塞进请求，多轮对话的请求体会迅速膨胀。因此在首次上传时就抽取成文本
    并缓存，后续轮次只发文本。
    """

    __tablename__ = "chat_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=True, index=True
    )

    #: image / pdf / docx / text
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: 文本抽取状态：pending / done / failed / not_applicable
    extract_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extract_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = _now_column()

    message: Mapped[ChatMessage | None] = relationship(back_populates="attachments")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ChatAttachment id={self.id} {self.kind} {self.filename!r}>"
