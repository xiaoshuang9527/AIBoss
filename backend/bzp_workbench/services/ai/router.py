"""意图路由：判断用户这句话属于哪种模式。

成本控制（重要）
----------------
路由需要一次额外的模型往返。对一个 1.5 秒延迟的模型来说，每轮都路由会让
交互明显变慢，也白白消耗 token。因此采用**惰性路由**：

* 会话还没定模式（首轮）→ 路由
* 用户显式要求切换模式 → 直接切，不问模型
* 已定模式且不是首轮 → **沿用当前模式**，不路由

话题跳转的情况交给用户手动切换（前端有覆盖入口），而不是每轮都猜。
这个取舍是刻意的：误判模式比多一次点击更让人恼火。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..llm import LLMError, LLMResponse, Message, Role
from ..logging_service import logger
from .prompts import ROUTER_PROMPT

#: 合法的模式取值
MODES = ("job_hunting", "interview_coach", "chat")

#: 会话未定模式时使用的哨兵值
UNSET_MODE = "unset"

#: 用户显式切换模式的触发词
_SWITCH_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(切换到|改用|换成|进入)?\s*(找岗位|找工作|求职|搜岗位|岗位搜索)"),
        "job_hunting",
    ),
    (
        re.compile(r"(切换到|改用|换成|进入)?\s*(面试辅导|面试准备|简历分析|模拟面试)"),
        "interview_coach",
    ),
    (re.compile(r"(切换到|改用|换成|进入)?\s*(闲聊|普通对话|聊天模式)"), "chat"),
)


@dataclass
class RouteDecision:
    """路由结果。"""

    mode: str
    reason: str = ""
    confidence: float = 0.0
    #: 是否真的调用了模型（便于统计成本）
    used_model: bool = False
    #: 是否由用户显式指定
    explicit: bool = False


def detect_explicit_switch(text: str) -> str | None:
    """识别用户显式的模式切换意图。"""
    if not text:
        return None
    for pattern, mode in _SWITCH_PATTERNS:
        if pattern.search(text):
            return mode
    return None


def parse_router_output(raw: str) -> tuple[str, str, float]:
    """解析模型返回的路由 JSON，容错各种脏输出。

    模型常见的三种脏输出都要能处理：
    1. 用 markdown 代码块包裹
    2. 前后带解释性文字
    3. 直接给个裸词（``job_hunting``）
    """
    text = (raw or "").strip()
    if not text:
        return "chat", "模型未返回内容", 0.0

    # 去掉 markdown 代码块围栏
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()

    payload: dict[str, Any] | None = None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            payload = parsed
    except (ValueError, TypeError):
        # 从文本里抠出第一个 JSON 对象
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    payload = parsed
            except (ValueError, TypeError):
                payload = None

    if payload is None:
        # 兜底：文本里直接出现模式名
        for mode in MODES:
            if mode in text:
                return mode, "从文本中提取", 0.5
        return "chat", "无法解析路由结果，回退为闲聊", 0.0

    mode = str(payload.get("intent") or payload.get("mode") or "chat").strip()
    if mode not in MODES:
        mode = "chat"
    reason = str(payload.get("reason") or "")
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return mode, reason, max(0.0, min(confidence, 1.0))


async def route(
    adapter: Any,
    user_text: str,
    *,
    current_mode: str = UNSET_MODE,
    is_first_turn: bool = False,
) -> RouteDecision:
    """决定本次对话使用哪种模式。

    ``adapter`` 是已经构造好的 LLM 适配器（由调用方保证可用）。
    """
    # 1) 用户显式切换优先
    explicit = detect_explicit_switch(user_text)
    if explicit:
        return RouteDecision(
            mode=explicit, reason="用户显式指定", confidence=1.0, explicit=True
        )

    # 2) 已有模式且不是首轮 → 沿用，省一次模型往返
    if current_mode in MODES and not is_first_turn:
        return RouteDecision(mode=current_mode, reason="沿用当前模式")

    # 3) 首轮或未定模式 → 调模型判断
    try:
        response: LLMResponse = await adapter.chat_completion(
            [
                Message.system(ROUTER_PROMPT),
                Message.user(user_text or "（用户只上传了附件，没有文字）"),
            ],
            temperature=0.0,
            max_tokens=200,
        )
    except LLMError as exc:
        # 路由失败不应该让对话失败：回退到闲聊模式，用户仍能得到回复
        logger.warning("意图路由失败，回退为 chat：%s", exc)
        return RouteDecision(mode="chat", reason=f"路由失败：{exc.message}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("意图路由异常，回退为 chat：%s", exc)
        return RouteDecision(mode="chat", reason=f"路由异常：{type(exc).__name__}")

    mode, reason, confidence = parse_router_output(response.text)
    return RouteDecision(
        mode=mode, reason=reason, confidence=confidence, used_model=True
    )


__all__ = [
    "MODES",
    "UNSET_MODE",
    "RouteDecision",
    "detect_explicit_switch",
    "parse_router_output",
    "route",
]
