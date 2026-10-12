"""AI 对话服务层。

P0 阶段包含：

* :mod:`attachments` —— 附件落盘、按内容嗅探类型、文本抽取
* :mod:`stream`      —— SSE 事件定义与编码
* :mod:`fake_driver` —— 假模型 echo 驱动（不需 API Key 即可跑通链路）
* :mod:`service`     —— 会话编排：上下文组装、流式、落库
"""

from .attachments import (
    MAX_ATTACHMENTS_PER_MESSAGE,
    MAX_DOC_BYTES,
    MAX_IMAGE_BYTES,
    AttachmentError,
    build_attachment_context,
    delete_conversation_files,
    extract_text,
    sniff_file,
    store_file,
    to_image_part,
    validate_size,
)
from .stream import SSE_HEADERS, SSEEvent, SSEFrame, encode_comment

__all__ = [
    "MAX_ATTACHMENTS_PER_MESSAGE",
    "MAX_DOC_BYTES",
    "MAX_IMAGE_BYTES",
    "SSE_HEADERS",
    "AttachmentError",
    "SSEEvent",
    "SSEFrame",
    "build_attachment_context",
    "delete_conversation_files",
    "encode_comment",
    "extract_text",
    "sniff_file",
    "store_file",
    "to_image_part",
    "validate_size",
]
