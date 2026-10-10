"""共享枚举。

刻意保持为普通 ``str`` 枚举，这样 SQLAlchemy 存字符串、Pydantic 出字符串，
前端拿到的就是可读值，不需要额外映射表。
"""

from __future__ import annotations

from enum import Enum


class TaskStatus(str, Enum):
    """抓取任务状态机。"""

    PENDING = "pending"          # 已入队，等待执行
    RUNNING = "running"          # 正在执行
    SUCCEEDED = "succeeded"      # 抓取完成
    FAILED = "failed"            # 执行失败（重试耗尽）
    CANCELLED = "cancelled"      # 用户取消
    # 需要人工介入：通常是登录态失效 / 触发风控验证
    NEEDS_ATTENTION = "needs_attention"

    @property
    def label(self) -> str:
        return {
            TaskStatus.PENDING: "排队中",
            TaskStatus.RUNNING: "进行中",
            TaskStatus.SUCCEEDED: "成功",
            TaskStatus.FAILED: "失败",
            TaskStatus.CANCELLED: "已取消",
            TaskStatus.NEEDS_ATTENTION: "待处理",
        }[self]

    @property
    def is_terminal(self) -> bool:
        return self in {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.NEEDS_ATTENTION,
        }

    @property
    def is_active(self) -> bool:
        return self in {TaskStatus.PENDING, TaskStatus.RUNNING}


class AuthStatus(str, Enum):
    """BOSS 直聘授权档案状态。"""

    UNKNOWN = "unknown"        # 尚未校验
    ACTIVE = "active"          # 登录态有效
    EXPIRED = "expired"        # 登录态失效，需要重新扫码
    AUTHORIZING = "authorizing"  # 正在等待用户扫码
    INVALID = "invalid"        # 档案损坏或目录缺失

    @property
    def label(self) -> str:
        return {
            AuthStatus.UNKNOWN: "未校验",
            AuthStatus.ACTIVE: "已授权",
            AuthStatus.EXPIRED: "已失效",
            AuthStatus.AUTHORIZING: "等待扫码",
            AuthStatus.INVALID: "异常",
        }[self]


class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class LogCategory(str, Enum):
    """系统日志分类，方便前端按模块过滤。"""

    SYSTEM = "system"
    AUTH = "auth"
    TASK = "task"
    SCRAPER = "scraper"
    EXPORT = "export"
