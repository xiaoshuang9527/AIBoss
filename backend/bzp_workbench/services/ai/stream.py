"""SSE 事件定义与编码。

为什么用 SSE 而不是 WebSocket
-----------------------------
对话是「一问一答」的单向流：服务端持续推，客户端只发一次请求。
SSE 天然贴合这个模型，且能复用现有的 Nginx 配置（只需关 ``proxy_buffering``），
不用像 WebSocket 那样单独处理升级握手与心跳。

事件类型（前端按 type 分支渲染）
--------------------------------
* ``meta``        —— 起始元信息：消息 id、所用模型、意图模式
* ``reasoning``   —— 思考过程增量（推理模型），前端折叠展示
* ``text``        —— 正文增量
* ``tool_call``   —— 模型发起工具调用
* ``tool_result`` —— 工具返回结果（含结构化载荷）
* ``usage``       —— token 用量
* ``done``        —— 正常结束
* ``error``       —— 失败（复用 llm/errors.py 的分类与修复建议）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SSEEvent(str, Enum):
    META = "meta"
    REASONING = "reasoning"
    TEXT = "text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    USAGE = "usage"
    DONE = "done"
    ERROR = "error"


@dataclass
class SSEFrame:
    """一个待发送的 SSE 帧。"""

    event: SSEEvent
    data: dict[str, Any] = field(default_factory=dict)

    def encode(self) -> str:
        """编码成 SSE 文本。

        注意 ``ensure_ascii=False``：中文必须原样输出，否则前端看到的
        是 ``\\u4f60\\u597d`` 这种转义序列。
        """
        payload = json.dumps(self.data, ensure_ascii=False)
        return f"event: {self.event.value}\ndata: {payload}\n\n"


def encode_comment(text: str) -> str:
    """SSE 注释行，用作心跳保活（不会触发前端事件）。"""
    return f": {text}\n\n"


#: SSE 响应头。``X-Accel-Buffering: no`` 用于让 Nginx 不要缓冲，
#: 否则流式会被攒成一整块才下发，首字延迟形同虚设。
SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


__all__ = ["SSEEvent", "SSEFrame", "SSE_HEADERS", "encode_comment"]
