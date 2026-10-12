"""DeepSeek 适配器。

DeepSeek 提供 OpenAI 兼容接口，因此继承 :class:`OpenAICompatAdapter`，
只声明默认值并加上它**特有的两条硬约束**的本地前置校验。

官方文档依据
------------
* 模型名 ``deepseek-flash`` 即最新的 V4.1 Flash（原生多模态），
  旧名 ``deepseek-v4-flash``、``deepseek-v4-flash-vision-exp`` 仍会路由到它。
  见 https://api-docs.deepseek.com/zh-cn/news/news260910/
* 图片只能出现在 **user** 消息中，``system`` / ``assistant`` 携带图片会返回 400。
  见 https://api-docs.deepseek.com/zh-cn/guides/vision/
* Chat Completion 接口**不支持在对话中间插入工具调用**（Anthropic / Responses
  接口才支持），因此工具调用只能由模型发起、按标准循环推进。
  见 https://api-docs.deepseek.com/zh-cn/guides/tool_calls

前两条在 :meth:`validate_messages` 里本地拦截，给出明确原因，
不让用户去猜一个 400 到底哪里错了。
"""

from __future__ import annotations

from ..capabilities import Capability
from .openai_compat import OpenAICompatAdapter
from ..base import Message, Role
from ..errors import LLMError, LLMErrorType


class DeepSeekAdapter(OpenAICompatAdapter):
    provider = "deepseek"
    display_name = "DeepSeek"
    default_base_url = "https://api.deepseek.com"
    requires_api_key = True
    supported_capabilities = frozenset(
        {
            Capability.TEXT.value,
            Capability.VISION.value,
            Capability.TOOL_CALLS.value,
            Capability.REASONING.value,
        }
    )
    doc_url = "https://api-docs.deepseek.com/zh-cn/"

    #: 当前推荐的默认模型
    suggested_models: tuple[str, ...] = (
        "deepseek-flash",
        "deepseek-v4-pro",
        "deepseek-reasoner",
    )

    def validate_messages(self, messages: list[Message]) -> None:
        """DeepSeek 特有约束的本地校验。"""
        for index, message in enumerate(messages):
            # 约束一：图片只能出现在 user 消息里
            if message.images and message.role is not Role.USER:
                raise LLMError(
                    LLMErrorType.INVALID_MESSAGES,
                    f"第 {index + 1} 条消息是 {message.role.value}，但携带了图片",
                    detail=(
                        "DeepSeek 接口只允许图片出现在 user 消息中，"
                        "system / assistant 消息携带图片会返回 400。"
                        "请把图片移到用户消息里（常见做法是在首次上传时就抽取成文本）。"
                    ),
                )

            # 约束二：tool 结果消息必须带 tool_call_id，否则供应商会拒绝
            if message.role is Role.TOOL and not message.tool_call_id:
                raise LLMError(
                    LLMErrorType.INVALID_MESSAGES,
                    f"第 {index + 1} 条是工具结果消息，但缺少 tool_call_id",
                    detail="工具结果必须与某次 tool_call 的 id 对应。",
                )

    @classmethod
    def metadata(cls) -> dict:
        data = super().metadata()
        data["suggested_models"] = list(cls.suggested_models)
        data["notes"] = [
            "V4.1 Flash 原生支持多模态，模型名用 deepseek-flash 即可",
            "图片只能放在 user 消息中（system/assistant 带图会返回 400）",
            "支持思考模式，可用 reasoning 能力标签标记",
        ]
        return data


__all__ = ["DeepSeekAdapter"]
