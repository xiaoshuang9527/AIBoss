"""工具的输入输出结构。

工具（Tool）是 AI 与工作台能力之间的唯一接口：模型只能通过工具读取岗位库、
发起抓取任务。把契约单独定义，工具实现、注册表、执行器都依赖它，
新增工具时只要实现 :class:`Tool` 即可。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ...llm import ToolDefinition


class ToolError(Exception):
    """工具执行失败。

    注意：工具的失败**不应该中断对话**——模型需要知道失败原因才能换策略
    （例如「库里没有数据」应该促使它改用 trigger_boss_scraper）。
    所以这里错误会被转成文本喂回模型，而不是抛到上层。
    """

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        self.message = message
        self.hint = hint
        super().__init__(message)

    def to_model_text(self) -> str:
        """转成给模型看的文本：说明失败原因 + 建议的下一步。"""
        text = f"工具执行失败：{self.message}"
        if self.hint:
            text += f"\n建议：{self.hint}"
        return text


@dataclass
class ToolResult:
    """工具执行结果。

    ``payload_type`` 决定前端用什么组件渲染：

    * ``job_cards``    —— 岗位卡片列表（含「去 BOSS 沟通」链接）
    * ``task_created`` —— 抓取任务已创建
    * ``resume``       —— 简历结构化结果
    * ``text``         —— 纯文本
    """

    payload_type: str = "text"
    data: Any = None
    #: 给模型看的文本摘要（模型据此决定下一步，不直接给用户看）
    model_text: str = ""
    #: 执行耗时（毫秒），用于前端展示与排查
    latency_ms: int = 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "payload_type": self.payload_type,
            "data": self.data,
            "latency_ms": self.latency_ms,
        }


@dataclass
class ToolContext:
    """工具执行所需的上下文（数据库会话、当前会话 id 等）。"""

    db: Any
    conversation_id: int
    model_config_id: int
    #: 会话的槽位台账，工具可以读（例如把用户意图补进去）
    slot_state: dict[str, Any] = field(default_factory=dict)


class Tool(ABC):
    """工具基类。

    子类需要声明 :attr:`name` / :attr:`description` / :attr:`parameters`，
    并实现 :meth:`run`。
    """

    name: str = ""
    description: str = ""
    #: JSON Schema（OpenAI function calling 格式）
    parameters: dict[str, Any] = {}
    #: 是否需要工具调用能力（几乎都是 True）
    requires_tool_calls: bool = True
    #: 该工具适用的意图模式；空集合表示所有模式都可用。
    #: 用它把「面试辅导」模式下不需要的岗位工具隐藏掉，减少模型误调用。
    modes: frozenset[str] = frozenset()

    def to_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters or {"type": "object", "properties": {}},
        )

    def applies_to(self, mode: str) -> bool:
        return not self.modes or mode in self.modes

    @abstractmethod
    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        """执行工具。失败时抛 :class:`ToolError`。"""

    # ------------------------------------------------------------------
    # 参数辅助
    # ------------------------------------------------------------------
    @staticmethod
    def get_str(arguments: dict[str, Any], key: str, default: str = "") -> str:
        value = arguments.get(key, default)
        return str(value).strip() if value is not None else default

    @staticmethod
    def get_int(arguments: dict[str, Any], key: str, default: int | None = None) -> int | None:
        value = arguments.get(key)
        if value is None or value == "":
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default


def dumps(data: Any) -> str:
    """把数据转成给模型看的紧凑 JSON。"""
    return json.dumps(data, ensure_ascii=False, default=str)


__all__ = ["Tool", "ToolContext", "ToolError", "ToolResult", "dumps"]
