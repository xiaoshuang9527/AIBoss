"""模型能力标签。

刻意**不含「图生图」**：当前所有已适配的供应商（DeepSeek 全系、OpenAI 兼容系）
都没有图像生成能力，保留该选项只会让用户勾选后在运行时报错。

新增能力时：在这里加常量 → 适配器声明支持 → 前端自动跟着出现（下拉与多选
的数据源都来自 ``/api/models/providers``，前端不硬编码）。
"""

from __future__ import annotations

from enum import Enum


class Capability(str, Enum):
    """模型能力标签。"""

    TEXT = "text"
    """文生文：基础对话与文本生成。"""

    VISION = "vision"
    """看图：理解图片输入（简历截图、图表等）。"""

    TOOL_CALLS = "tool_calls"
    """工具调用：Function Calling，用于触发爬虫等外部动作。"""

    REASONING = "reasoning"
    """推理：支持思考模式 / 深度推理。"""

    @property
    def label(self) -> str:
        return {
            Capability.TEXT: "文生文",
            Capability.VISION: "看图",
            Capability.TOOL_CALLS: "工具调用",
            Capability.REASONING: "推理",
        }[self]

    @property
    def description(self) -> str:
        return {
            Capability.TEXT: "基础对话与文本生成，所有模型都应具备",
            Capability.VISION: "可理解图片输入，如简历截图、图表",
            Capability.TOOL_CALLS: "支持 Function Calling，可触发抓取等外部动作",
            Capability.REASONING: "支持思考模式，适合复杂推理与规划",
        }[self]


# 供前端渲染的元数据（顺序即展示顺序）
CAPABILITY_CATALOG: tuple[Capability, ...] = (
    Capability.TEXT,
    Capability.VISION,
    Capability.TOOL_CALLS,
    Capability.REASONING,
)


def all_capability_values() -> set[str]:
    return {item.value for item in Capability}


def normalize_capabilities(raw: list[str] | str | None) -> list[str]:
    """把任意输入归一化成合法的能力列表（去重、保序、丢弃未知项）。

    容忍字符串输入（逗号分隔），因为数据库里存的是 JSON 文本，
    调用方可能直接把原始字符串递进来。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        import json

        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            parsed = [item.strip() for item in text.split(",")]
        raw = parsed if isinstance(parsed, list) else []

    valid = all_capability_values()
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        value = str(item).strip().lower()
        if value in valid and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def capabilities_to_json(values: list[str]) -> str:
    """序列化成数据库存储用的 JSON 文本。"""
    import json

    return json.dumps(normalize_capabilities(values), ensure_ascii=False)


def missing_capabilities(configured: list[str] | str | None, required: set[str]) -> set[str]:
    """返回 ``required`` 中未被 ``configured`` 覆盖的部分。"""
    have = set(normalize_capabilities(configured))
    return {item for item in required if item not in have}


def capability_catalog_payload() -> list[dict[str, str]]:
    """给前端的可选能力清单。"""
    return [
        {"value": item.value, "label": item.label, "description": item.description}
        for item in CAPABILITY_CATALOG
    ]


__all__ = [
    "CAPABILITY_CATALOG",
    "Capability",
    "all_capability_values",
    "capabilities_to_json",
    "capability_catalog_payload",
    "missing_capabilities",
    "normalize_capabilities",
]
