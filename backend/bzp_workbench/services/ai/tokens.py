"""Token 估算与上下文预算裁剪。

为什么要自己估算而不引入 tokenizer
----------------------------------
精确计数需要模型对应的 tokenizer（DeepSeek 的还要单独申请）。为了一个
"裁剪决策"引入这样的依赖不划算，而且 tokenizer 本身也要占内存/启动时间。

这里用**保守的字符比例估算**：宁可高估，因为高估只会让裁剪更早触发
（多丢一点历史），而低估会导致请求超限、直接失败。

为什么需要它（实测数据）
------------------------
真实请求的 prompt 构成（job_hunting 模式，见
``tests/manual_measure_context.py``）::

    System Prompt    :  985 token
    工具定义（5 个）  : 1560 token   ← 比 System Prompt 还大
    历史消息         :  441 token（仅 3 条）

也就是说**固定开销占了大部分**。但真正的风险在历史：一次岗位检索会把
10 条岗位的完整卡片写进 ``content_json``（实测单个消息 9187 字），
而给模型的文本也有一两千字。多轮检索之后，40 条消息的上限完全挡不住
token 爆炸 —— 这正是只按"条数"裁剪的缺陷。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 每 token 对应多少字符（混合中英文的经验值）
#:
#: 中文约 1 字/token，英文约 4 字符/token。内容以中文为主、夹带技术英文与
#: JSON，取 1.6 是比较保守的估计。
CHARS_PER_TOKEN = 1.6

#: 单次请求的 prompt token 预算。
#:
#: DeepSeek 的上下文窗口足够大，但**不该把窗口塞满**：prompt 越大越慢、
#: 越贵，而久远的历史对当前回答的价值很低。留出余量给本轮的工具结果
#: （工具返回的文本可能很长，且这一轮必须完整发给模型）。
PROMPT_TOKEN_BUDGET = 24_000

#: 历史消息占预算的比例。剩下的留给 system prompt + 工具定义 + 本轮工具结果。
HISTORY_BUDGET_RATIO = 0.55

#: 无论如何都保留的最近消息条数（保证对话连贯性）
MIN_RECENT_MESSAGES = 6

#: 单条工具结果在**历史**里的字符上限。
#:
#: 完整工具结果只在本轮有意义（模型据此作答）；到了下一轮，把它压缩成
#: 摘要即可 —— 用户想再看详情可以重新检索，而模型只需要知道"当时查到了什么"。
MAX_TOOL_RESULT_CHARS_IN_HISTORY = 600


def estimate_tokens(text: str | None) -> int:
    """估算一段文本的 token 数（偏保守，宁可高估）。"""
    if not text:
        return 0
    return int(len(text) / CHARS_PER_TOKEN) + 1


def estimate_messages_tokens(messages: list) -> int:
    """估算一组消息的总 token。"""
    total = 0
    for message in messages:
        total += estimate_tokens(getattr(message, "content", None))
        # 多模态图片按固定成本粗估（真实计费按图像 token 数，这里只用于裁剪决策）
        images = getattr(message, "images", None) or []
        total += len(images) * 300
    return total


@dataclass
class TrimReport:
    """裁剪结果，便于排查"为什么模型不记得前面的事了"。"""

    messages: list
    dropped_count: int = 0
    compressed_count: int = 0
    estimated_tokens: int = 0
    budget: int = 0

    @property
    def trimmed(self) -> bool:
        return self.dropped_count > 0 or self.compressed_count > 0


def compress_tool_result(text: str | None, limit: int = MAX_TOOL_RESULT_CHARS_IN_HISTORY) -> str:
    """把历史里的工具结果压缩成摘要。

    不加这个的话：一次岗位检索的工具结果有 2000+ 字，之后**每一轮**都会
    重发，几轮下来 prompt 就被历史撑爆了。
    """
    raw = text or ""
    if len(raw) <= limit:
        return raw
    return raw[:limit] + f"\n…（历史记录已压缩，原文 {len(raw)} 字）"


def trim_messages(
    messages: list,
    *,
    system_messages: list | None = None,
    budget: int = PROMPT_TOKEN_BUDGET,
) -> TrimReport:
    """按 token 预算裁剪消息，而不是按条数。

    策略（按优先级从高到低保留）：

    1. **system prompt 永不丢弃** —— 丢了模型就失忆了
    2. **最近 ``MIN_RECENT_MESSAGES`` 条永不丢弃** —— 保证对话连贯
    3. 从最旧的开始丢，直到落进预算
    4. 丢弃前先尝试**压缩**历史里的 tool 结果（信息量低、体积大）

    参数
    ----
    messages:
        按时间正序的对话消息（不含 system）
    system_messages:
        system 消息，单独传入以便永远保留
    budget:
        总 token 预算
    """
    systems = list(system_messages or [])
    system_tokens = estimate_messages_tokens(systems)
    history_budget = int(budget * HISTORY_BUDGET_RATIO)
    # 预算不够时至少给历史留一点，避免退化成"完全没有上下文"
    history_budget = max(history_budget, 500)

    working = list(messages)
    compressed = 0

    # ---- 第一步：压缩历史里的 tool 结果 ----
    for message in working:
        if getattr(message, "role", None) and message.role.value == "tool":
            original = message.content or ""
            shortened = compress_tool_result(original)
            if shortened != original:
                message.content = shortened
                compressed += 1

    # ---- 第二步：从最旧的开始丢，但保住最近若干条 ----
    dropped = 0
    while working:
        total = system_tokens + estimate_messages_tokens(working)
        if total <= budget:
            break
        if len(working) <= MIN_RECENT_MESSAGES:
            # 已经只剩最少保留了，再丢就伤连贯性 —— 宁可超预算
            break
        working.pop(0)
        dropped += 1

    return TrimReport(
        messages=systems + working,
        dropped_count=dropped,
        compressed_count=compressed,
        estimated_tokens=system_tokens + estimate_messages_tokens(working),
        budget=budget,
    )


__all__ = [
    "CHARS_PER_TOKEN",
    "HISTORY_BUDGET_RATIO",
    "MAX_TOOL_RESULT_CHARS_IN_HISTORY",
    "MIN_RECENT_MESSAGES",
    "PROMPT_TOKEN_BUDGET",
    "TrimReport",
    "compress_tool_result",
    "estimate_messages_tokens",
    "estimate_tokens",
    "trim_messages",
]
