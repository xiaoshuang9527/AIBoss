"""工具注册表。

新增工具的步骤：
1. 在 ``tools/`` 下实现一个 :class:`~....tools.base.Tool` 子类；
2. 在下面的 ``_TOOLS`` 里注册。

工具按意图模式过滤：面试辅导模式下不会向模型暴露岗位查询工具，
减少误调用（模型看到什么工具就会倾向调用什么）。
"""

from __future__ import annotations

from .base import Tool, ToolContext, ToolError, ToolResult
from .search_jobs import SearchExistingJobsTool
from .task_status import CheckTaskStatusTool
from .trigger_scraper import TriggerBossScraperTool

#: 已注册的工具。
#:
#: 注意 ``TriggerBossScraperTool`` 的 modes 只有 job_hunting —— 抓取是有副作用的
#: 重操作（10-20 分钟，且高频抓取会触发风控），不该在通用对话里被误调用。
_TOOLS: tuple[Tool, ...] = (
    SearchExistingJobsTool(),
    TriggerBossScraperTool(),
    CheckTaskStatusTool(),
)

_REGISTRY: dict[str, Tool] = {tool.name: tool for tool in _TOOLS}


def all_tools() -> tuple[Tool, ...]:
    return _TOOLS


def tools_for_mode(mode: str) -> list[Tool]:
    """按意图模式筛选可用工具。"""
    return [tool for tool in _TOOLS if tool.applies_to(mode)]


def get_tool(name: str) -> Tool | None:
    return _REGISTRY.get(name)


def tool_names() -> list[str]:
    return sorted(_REGISTRY)


__all__ = [
    "CheckTaskStatusTool",
    "SearchExistingJobsTool",
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolResult",
    "TriggerBossScraperTool",
    "all_tools",
    "get_tool",
    "tool_names",
    "tools_for_mode",
]
