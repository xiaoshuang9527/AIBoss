"""测试：AI 对话模块 P0 骨架。

覆盖：
* 附件按**文件内容**嗅探类型（不信扩展名与声明的 MIME）
* 大小限制与数量限制
* 会话 CRUD 与删除时**清理附件文件**
* SSE 流式事件的时序与内容（思考过程 / 正文 / 用量 / 结束）
* 假模型 echo 不访问网络即可产生完整事件流
"""

from __future__ import annotations

import io
import json
import struct
import zlib

import pytest


# ============================================================
# 构造测试用文件
# ============================================================
def make_png(width: int = 4, height: int = 4) -> bytes:
    """生成一个最小合法 PNG（真实文件头，用于验证魔数嗅探）。"""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def make_minimal_pdf(text: str = "Hello PDF") -> bytes:
    """生成一个能被 pdfplumber 打开的最小 PDF。"""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    return bytes(out)


def make_docx(paragraphs: list[str]) -> bytes:
    """用 python-docx 生成一个真实 docx。"""
    import docx

    document = docx.Document()
    for line in paragraphs:
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ============================================================
# 附件嗅探（纯函数，不需要数据库）
# ============================================================
class TestSniffFile:
    def test_detects_png_by_magic(self):
        from bzp_workbench.services.ai import sniff_file

        result = sniff_file(make_png(), "whatever.bin")
        assert result.kind == "image"
        assert result.mime_type == "image/png"

    def test_detects_jpeg_by_magic(self):
        from bzp_workbench.services.ai import sniff_file

        result = sniff_file(b"\xff\xd8\xff\xe0" + b"\x00" * 32, "photo.jpg")
        assert result.kind == "image"
        assert result.mime_type == "image/jpeg"

    def test_detects_webp_by_magic(self):
        from bzp_workbench.services.ai import sniff_file

        data = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"VP8 " + b"\x00" * 16
        assert sniff_file(data, "a.webp").mime_type == "image/webp"

    def test_detects_pdf_by_magic(self):
        from bzp_workbench.services.ai import sniff_file

        result = sniff_file(make_minimal_pdf(), "resume.pdf")
        assert result.kind == "pdf"
        assert result.mime_type == "application/pdf"

    def test_detects_docx_by_inner_structure(self):
        from bzp_workbench.services.ai import sniff_file

        result = sniff_file(make_docx(["简历"]), "resume.docx")
        assert result.kind == "docx"

    def test_does_not_trust_extension(self):
        """把 PNG 改名成 .pdf，必须仍按内容识别为图片。"""
        from bzp_workbench.services.ai import sniff_file

        result = sniff_file(make_png(), "fake_resume.pdf")
        assert result.kind == "image", "必须按内容判断，不能信扩展名"

    def test_detects_plain_text(self):
        from bzp_workbench.services.ai import sniff_file

        result = sniff_file("这是一份纯文本简历".encode("utf-8"), "resume.txt")
        assert result.kind == "text"

    def test_rejects_binary_garbage(self):
        from bzp_workbench.services.ai import sniff_file

        result = sniff_file(b"\x00\x01\x02\xff\xfe" * 100, "mystery.dat")
        assert result.kind == "unsupported"

    def test_zip_that_is_not_docx_is_unsupported(self):
        import zipfile

        from bzp_workbench.services.ai import sniff_file

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("hello.txt", "not a word doc")
        assert sniff_file(buffer.getvalue(), "archive.zip").kind == "unsupported"


class TestValidateSize:
    def test_image_over_limit_rejected(self):
        from bzp_workbench.services.ai import AttachmentError, validate_size

        with pytest.raises(AttachmentError) as excinfo:
            validate_size("image", 9 * 1024 * 1024)
        assert "图片过大" in str(excinfo.value)

    def test_image_within_limit_ok(self):
        from bzp_workbench.services.ai import validate_size

        validate_size("image", 1024)

    def test_doc_over_limit_rejected(self):
        from bzp_workbench.services.ai import AttachmentError, validate_size

        with pytest.raises(AttachmentError) as excinfo:
            validate_size("pdf", 21 * 1024 * 1024)
        assert "文档过大" in str(excinfo.value)

    def test_unsupported_kind_rejected(self):
        from bzp_workbench.services.ai import AttachmentError, validate_size

        with pytest.raises(AttachmentError) as excinfo:
            validate_size("unsupported", 1024)
        assert "不支持的文件类型" in str(excinfo.value)


# ============================================================
# 文本抽取
# ============================================================
class TestExtractText:
    def test_image_is_not_applicable(self, tmp_path):
        from bzp_workbench.services.ai import extract_text

        path = tmp_path / "a.png"
        path.write_bytes(make_png())
        text, status = extract_text(path, "image")
        assert status == "not_applicable"
        assert text == ""

    def test_plain_text(self, tmp_path):
        from bzp_workbench.services.ai import extract_text

        path = tmp_path / "a.txt"
        path.write_text("张三，5 年 Python 经验", encoding="utf-8")
        text, status = extract_text(path, "text")
        assert status == "done"
        assert "Python" in text

    def test_gbk_text_is_decoded(self, tmp_path):
        from bzp_workbench.services.ai import extract_text

        path = tmp_path / "gbk.txt"
        path.write_bytes("中文简历".encode("gb18030"))
        text, status = extract_text(path, "text")
        assert status == "done"
        assert "中文简历" in text

    def test_docx_extraction(self, tmp_path):
        from bzp_workbench.services.ai import extract_text

        path = tmp_path / "a.docx"
        path.write_bytes(make_docx(["张三", "技能：Python、FastAPI"]))
        text, status = extract_text(path, "docx")
        assert status == "done"
        assert "张三" in text and "FastAPI" in text

    def test_pdf_extraction(self, tmp_path):
        from bzp_workbench.services.ai import extract_text

        path = tmp_path / "a.pdf"
        path.write_bytes(make_minimal_pdf("Zhang San Python"))
        text, status = extract_text(path, "pdf")
        assert status == "done"
        assert "Zhang San" in text

    def test_corrupt_file_fails_gracefully(self, tmp_path):
        """抽取失败不能让上传整体失败。"""
        from bzp_workbench.services.ai import extract_text

        path = tmp_path / "broken.pdf"
        path.write_bytes(b"%PDF-1.4\n garbage not a real pdf")
        text, status = extract_text(path, "pdf")
        assert status.startswith("failed:")
        assert text == ""


# ============================================================
# 假模型驱动
# ============================================================
class TestFakeDriver:
    async def test_streams_reasoning_then_text(self):
        from bzp_workbench.services.ai.fake_driver import FakeLLMDriver
        from bzp_workbench.services.llm import Message, StreamEventType

        driver = FakeLLMDriver()
        driver.text_delay = 0
        driver.reasoning_delay = 0

        order: list[str] = []
        text, reasoning = [], []
        async for chunk in driver.stream_chat([Message.user("你好，介绍一下你自己")]):
            order.append(chunk.type.value)
            if chunk.type is StreamEventType.TEXT:
                text.append(chunk.text)
            elif chunk.type is StreamEventType.REASONING:
                reasoning.append(chunk.text)

        # 思考过程必须出现在正文之前 —— 这正是真实推理模型的行为
        first_text = order.index("text")
        assert "reasoning" in order[:first_text], "思考过程应先于正文"
        assert reasoning and text
        assert "P0" in "".join(text) or "骨架" in "".join(text)
        assert order[-1] == "done"

    async def test_job_query_triggers_tool_call(self):
        from bzp_workbench.services.ai.fake_driver import FakeLLMDriver
        from bzp_workbench.services.llm import Message, StreamEventType

        driver = FakeLLMDriver()
        driver.text_delay = 0
        driver.reasoning_delay = 0

        calls = []
        async for chunk in driver.stream_chat([Message.user("帮我在深圳找 Python 岗位")]):
            if chunk.type is StreamEventType.TOOL_CALL and chunk.tool_call:
                calls.append(chunk.tool_call)

        assert len(calls) == 1
        assert calls[0].name == "search_existing_jobs"
        args = calls[0].parsed_arguments()
        assert args["city"] == "深圳"
        assert args["keyword"] == "Python"

    async def test_non_job_query_has_no_tool_call(self):
        from bzp_workbench.services.ai.fake_driver import FakeLLMDriver
        from bzp_workbench.services.llm import Message, StreamEventType

        driver = FakeLLMDriver()
        driver.text_delay = 0
        driver.reasoning_delay = 0
        types = set()
        async for chunk in driver.stream_chat([Message.user("解释一下什么是 REST")]):
            types.add(chunk.type)
        assert StreamEventType.TOOL_CALL not in types

    async def test_chat_completion_returns_usage(self):
        from bzp_workbench.services.ai.fake_driver import FakeLLMDriver
        from bzp_workbench.services.llm import Message

        response = await FakeLLMDriver().chat_completion([Message.user("hi")])
        assert response.text
        assert response.usage.prompt_tokens > 0
        assert response.model == "fake-echo"


# ============================================================
# SSE 编码
# ============================================================
class TestSSEEncoding:
    def test_frame_format(self):
        from bzp_workbench.services.ai import SSEEvent, SSEFrame

        frame = SSEFrame(SSEEvent.TEXT, {"delta": "你好"})
        encoded = frame.encode()
        assert encoded.startswith("event: text\n")
        assert encoded.endswith("\n\n")
        # 中文必须原样输出，不能是 \uXXXX 转义
        assert "你好" in encoded
        assert "\\u" not in encoded

    def test_payload_is_valid_json(self):
        from bzp_workbench.services.ai import SSEEvent, SSEFrame

        encoded = SSEFrame(SSEEvent.USAGE, {"prompt_tokens": 12}).encode()
        data_line = [line for line in encoded.splitlines() if line.startswith("data: ")][0]
        assert json.loads(data_line[len("data: ") :]) == {"prompt_tokens": 12}

    def test_headers_disable_proxy_buffering(self):
        from bzp_workbench.services.ai import SSE_HEADERS

        # 少了这个头，Nginx 会把流攒成一整块再下发，首字延迟形同虚设
        assert SSE_HEADERS["X-Accel-Buffering"] == "no"
        assert "no-cache" in SSE_HEADERS["Cache-Control"]


# ============================================================
# API 集成
# ============================================================
@pytest.fixture(autouse=True)
async def _clean_chat_tables():
    """清空对话相关表，并**保证存在一个可用模型**。

    对话链路需要 `resolve_model_config` 能选出一个启用的模型；测试库里
    不一定有（模型管理测试会把它清掉），所以这里显式播种一个。
    P0 用假模型驱动，因此这个 API Key 是占位值，不会真的被使用。
    """
    from sqlalchemy import delete, select

    from bzp_workbench.db import SessionLocal, init_db
    from bzp_workbench.models import (
        ChatAttachment,
        ChatConversation,
        ChatMessage,
        ModelConfig,
    )
    from bzp_workbench.services.llm.crypto import encrypt_api_key

    await init_db()
    async with SessionLocal() as session:
        await session.execute(delete(ChatAttachment))
        await session.execute(delete(ChatMessage))
        await session.execute(delete(ChatConversation))

        has_model = (
            await session.execute(
                select(ModelConfig).where(ModelConfig.is_enabled.is_(True)).limit(1)
            )
        ).scalar_one_or_none()
        if has_model is None:
            session.add(
                ModelConfig(
                    display_name="测试模型",
                    provider="deepseek",
                    model_id="deepseek-flash",
                    base_url="https://api.deepseek.com",
                    api_key_encrypted=encrypt_api_key("sk-test-placeholder"),
                    capabilities='["text", "vision", "tool_calls"]',
                    is_enabled=True,
                    is_default=True,
                )
            )
        await session.commit()
    yield


@pytest.fixture
async def conversation(client) -> int:
    response = await client.post("/api/chat/conversations", data={"title": "测试会话"})
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def parse_sse(body: str) -> list[tuple[str, dict]]:
    """解析 SSE 文本成 (event, data) 列表。"""
    frames: list[tuple[str, dict]] = []
    for block in body.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        event_name = None
        payload = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line[len("event: ") :]
            elif line.startswith("data: "):
                payload = json.loads(line[len("data: ") :])
        if event_name:
            frames.append((event_name, payload or {}))
    return frames


class TestChatApi:
    async def test_config_endpoint(self, client):
        response = await client.get("/api/chat/config")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["max_attachments"] == 5
        assert data["fake_llm"] is True
        # _human_size 会在数字与单位间留一个空格
        assert data["max_image_label"].replace(" ", "") == "8.0MB"
        assert data["max_doc_label"].replace(" ", "") == "20.0MB"

    async def test_create_and_list_conversations(self, client, conversation):
        response = await client.get("/api/chat/conversations")
        assert response.status_code == 200
        rows = response.json()["data"]
        assert any(row["id"] == conversation for row in rows)

    async def test_get_conversation_detail(self, client, conversation):
        response = await client.get(f"/api/chat/conversations/{conversation}")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["conversation"]["id"] == conversation
        assert data["messages"] == []
        assert data["attachments"] == []

    async def test_missing_conversation_404(self, client):
        assert (await client.get("/api/chat/conversations/999999")).status_code == 404

    async def test_empty_message_rejected(self, client, conversation):
        response = await client.post(
            f"/api/chat/conversations/{conversation}/messages", json={}
        )
        assert response.status_code == 422

    async def test_stream_produces_expected_event_order(self, client, conversation):
        response = await client.post(
            f"/api/chat/conversations/{conversation}/messages",
            json={"content": "你好，请简单介绍一下自己"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        # 反代缓冲必须关掉
        assert response.headers.get("x-accel-buffering") == "no"

        frames = parse_sse(response.text)
        names = [name for name, _ in frames]

        assert names[0] == "meta"
        assert names[-1] == "done"
        assert "reasoning" in names, "推理模型的思考过程应单独成流"
        assert "text" in names
        assert "usage" in names

        # meta 里要带上所用模型，前端才能显示"正在用哪个模型回答"
        meta = frames[0][1]
        assert meta["conversation_id"] == conversation
        assert meta["model_label"]
        assert meta["fake"] is True

        # 思考过程必须早于正文
        assert names.index("reasoning") < names.index("text")

        # 正文能拼出完整回复
        text = "".join(data.get("delta", "") for name, data in frames if name == "text")
        assert len(text) > 20

        usage = [data for name, data in frames if name == "usage"][0]
        assert usage["prompt_tokens"] > 0
        assert usage["latency_ms"] >= 0

    async def test_stream_job_query_emits_tool_call(self, client, conversation):
        response = await client.post(
            f"/api/chat/conversations/{conversation}/messages",
            json={"content": "帮我在深圳找 Python 岗位"},
        )
        frames = parse_sse(response.text)
        tool_frames = [data for name, data in frames if name == "tool_call"]
        assert len(tool_frames) == 1
        assert tool_frames[0]["name"] == "search_existing_jobs"
        assert tool_frames[0]["arguments"]["city"] == "深圳"

    async def test_messages_are_persisted(self, client, conversation):
        await client.post(
            f"/api/chat/conversations/{conversation}/messages",
            json={"content": "第一条消息"},
        )
        detail = (await client.get(f"/api/chat/conversations/{conversation}")).json()["data"]
        roles = [item["role"] for item in detail["messages"]]
        assert roles == ["user", "assistant"]

        assistant = detail["messages"][1]
        assert assistant["content"]
        # 思考过程要落库，刷新页面后还能展开回看
        assert assistant["reasoning"]
        assert assistant["model_label"]

    async def test_conversation_title_derived_from_first_message(self, client, conversation):
        await client.post(
            f"/api/chat/conversations/{conversation}/messages",
            json={"content": "帮我分析一下简历里的技能缺口"},
        )
        detail = (await client.get(f"/api/chat/conversations/{conversation}")).json()["data"]
        # 夹具已经给了显式标题「测试会话」，不能被覆盖
        assert detail["conversation"]["title"] == "测试会话"

    async def test_auto_title_when_untitled(self, client):
        """没有显式标题时，用首条用户消息生成，避免列表里全是「新对话」。"""
        created = await client.post(
            "/api/chat/conversations", data={"title": "新对话"}
        )
        conversation_id = created.json()["data"]["id"]
        await client.post(
            f"/api/chat/conversations/{conversation_id}/messages",
            json={"content": "帮我分析一下简历里的技能缺口"},
        )
        detail = (
            await client.get(f"/api/chat/conversations/{conversation_id}")
        ).json()["data"]
        assert detail["conversation"]["title"] != "新对话"
        assert "简历" in detail["conversation"]["title"]

    async def test_history_is_sent_to_model(self, client, conversation):
        """第二轮对话应带上第一轮历史（否则模型没有上下文）。"""
        await client.post(
            f"/api/chat/conversations/{conversation}/messages",
            json={"content": "我叫小明"},
        )
        response = await client.post(
            f"/api/chat/conversations/{conversation}/messages",
            json={"content": "我叫什么"},
        )
        frames = parse_sse(response.text)
        # 假模型会把最后一条用户消息回显在思考过程里
        reasoning = "".join(
            data.get("delta", "") for name, data in frames if name == "reasoning"
        )
        assert "我叫什么" in reasoning


class TestAttachmentApi:
    async def test_upload_image_and_sniff_by_content(self, client, conversation):
        response = await client.post(
            f"/api/chat/conversations/{conversation}/attachments",
            files={"file": ("resume.pdf", make_png(), "application/pdf")},
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        # 文件名与 MIME 都说是 PDF，但内容是 PNG → 必须识别为图片
        assert data["kind"] == "image"
        assert data["mime_type"] == "image/png"
        assert data["extract_status"] == "not_applicable"
        assert data["size_label"]

    async def test_upload_docx_extracts_text(self, client, conversation):
        response = await client.post(
            f"/api/chat/conversations/{conversation}/attachments",
            files={
                "file": (
                    "resume.docx",
                    make_docx(["张三", "5 年 Python 经验"]),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert response.status_code == 200, response.text
        attachment_id = response.json()["data"]["id"]

        detail = (await client.get(f"/api/chat/attachments/{attachment_id}")).json()["data"]
        assert detail["kind"] == "docx"
        assert detail["extract_status"] == "done"
        assert "张三" in detail["extracted_text"]

    async def test_upload_pdf_extracts_text(self, client, conversation):
        response = await client.post(
            f"/api/chat/conversations/{conversation}/attachments",
            files={"file": ("resume.pdf", make_minimal_pdf("Senior Python Dev"), "application/pdf")},
        )
        assert response.status_code == 200, response.text
        attachment_id = response.json()["data"]["id"]
        detail = (await client.get(f"/api/chat/attachments/{attachment_id}")).json()["data"]
        assert detail["kind"] == "pdf"
        assert "Senior Python Dev" in detail["extracted_text"]

    async def test_reject_unsupported_file(self, client, conversation):
        response = await client.post(
            f"/api/chat/conversations/{conversation}/attachments",
            files={"file": ("weird.dat", b"\x00\x01\x02\xff" * 100, "application/octet-stream")},
        )
        assert response.status_code == 422
        assert "不支持的文件类型" in response.json()["detail"]

    async def test_reject_oversized_image(self, client, conversation):
        oversized = make_png() + b"\x00" * (9 * 1024 * 1024)
        response = await client.post(
            f"/api/chat/conversations/{conversation}/attachments",
            files={"file": ("big.png", oversized, "image/png")},
        )
        assert response.status_code == 422
        assert "图片过大" in response.json()["detail"]

    async def test_reject_empty_file(self, client, conversation):
        response = await client.post(
            f"/api/chat/conversations/{conversation}/attachments",
            files={"file": ("empty.png", b"", "image/png")},
        )
        assert response.status_code == 422

    async def test_raw_endpoint_serves_file(self, client, conversation):
        png = make_png()
        uploaded = await client.post(
            f"/api/chat/conversations/{conversation}/attachments",
            files={"file": ("a.png", png, "image/png")},
        )
        attachment_id = uploaded.json()["data"]["id"]
        response = await client.get(f"/api/chat/attachments/{attachment_id}/raw")
        assert response.status_code == 200
        assert response.content == png

    async def test_attachment_too_many_rejected(self, client, conversation):
        ids = []
        for index in range(5):
            uploaded = await client.post(
                f"/api/chat/conversations/{conversation}/attachments",
                files={"file": (f"a{index}.png", make_png(), "image/png")},
            )
            ids.append(uploaded.json()["data"]["id"])
        ids.append(ids[0])

        response = await client.post(
            f"/api/chat/conversations/{conversation}/messages",
            json={"content": "看图", "attachment_ids": ids + [ids[0], ids[0]]},
        )
        assert response.status_code == 422
        assert "附件" in response.json()["detail"]

    async def test_message_records_attachment_ids(self, client, conversation):
        uploaded = await client.post(
            f"/api/chat/conversations/{conversation}/attachments",
            files={"file": ("a.png", make_png(), "image/png")},
        )
        attachment_id = uploaded.json()["data"]["id"]

        await client.post(
            f"/api/chat/conversations/{conversation}/messages",
            json={"content": "这是我简历的截图", "attachment_ids": [attachment_id]},
        )
        detail = (await client.get(f"/api/chat/conversations/{conversation}")).json()["data"]
        user_message = detail["messages"][0]
        assert user_message["attachments"] == [attachment_id]

    async def test_image_only_message_allowed(self, client, conversation):
        """只发图片不带文字，应当允许。"""
        uploaded = await client.post(
            f"/api/chat/conversations/{conversation}/attachments",
            files={"file": ("a.png", make_png(), "image/png")},
        )
        attachment_id = uploaded.json()["data"]["id"]
        response = await client.post(
            f"/api/chat/conversations/{conversation}/messages",
            json={"content": "", "attachment_ids": [attachment_id]},
        )
        assert response.status_code == 200
        assert "text" in [name for name, _ in parse_sse(response.text)]


class TestModePersistence:
    """意图模式必须真的落库。

    这是一条**回归测试**：曾经出现过「路由判断出模式、赋值、commit，
    但 SQL 里没有 UPDATE」的隐蔽 bug —— 因为传入 stream_reply 的会话对象
    已经不在 session 上（detached），SQLAlchemy 不会为 detached 对象生成 flush。

    后果很隐蔽也很贵：模式每轮都会被重新路由，白白多一次模型调用，
    而且界面上模式标签一直停在旧值、看起来"AI 判断不准"。
    """

    async def test_mode_is_persisted_after_first_message(self, client, conversation):
        # 传 mode 强制指定：假模型驱动不具备真实分类能力，
        # 这里要验证的是「模式是否落库」，不是「分类准不准」。
        await client.post(
            f"/api/chat/conversations/{conversation}/messages",
            json={"content": "帮我在深圳找 Python 岗位", "mode": "job_hunting"},
        )
        detail = (await client.get(f"/api/chat/conversations/{conversation}")).json()["data"]
        assert detail["conversation"]["mode"] == "job_hunting", (
            "路由出的模式必须落库，否则下一轮会重复路由"
        )

    async def test_second_turn_reuses_mode_without_rerouting(self, client, conversation):
        """第二轮必须「沿用当前模式」，不能再调模型判断意图。"""
        from unittest.mock import patch as mock_patch

        await client.post(
            f"/api/chat/conversations/{conversation}/messages",
            json={"content": "帮我在深圳找 Python 岗位", "mode": "job_hunting"},
        )

        # 第二轮：路由函数应该直接返回「沿用」，不经过模型
        from bzp_workbench.services.ai import service as chat_service

        captured: list[str] = []
        original = chat_service.route

        async def spy_route(*args, **kwargs):
            result = await original(*args, **kwargs)
            captured.append(result.reason)
            return result

        with mock_patch.object(chat_service, "route", spy_route):
            await client.post(
                f"/api/chat/conversations/{conversation}/messages",
                json={"content": "那薪资呢"},
            )

        assert captured, "应调用过路由"
        assert captured[0] == "沿用当前模式", f"第二轮不该重路由，实际 reason={captured[0]!r}"

    async def test_manual_mode_switch_persists(self, client, conversation):
        response = await client.patch(
            f"/api/chat/conversations/{conversation}",
            data={"mode": "interview_coach", "reset_slots": True},
        )
        assert response.status_code == 200
        assert response.json()["data"]["mode"] == "interview_coach"

        detail = (await client.get(f"/api/chat/conversations/{conversation}")).json()["data"]
        assert detail["conversation"]["mode"] == "interview_coach"

    async def test_auto_mode_resets_to_unset(self, client, conversation):
        """选「自动判断」应清空模式，让下一轮重新路由。"""
        await client.patch(
            f"/api/chat/conversations/{conversation}", data={"mode": "chat"}
        )
        response = await client.patch(
            f"/api/chat/conversations/{conversation}", data={"mode": "auto"}
        )
        assert response.json()["data"]["mode"] == "unset"

    async def test_invalid_mode_rejected(self, client, conversation):
        response = await client.patch(
            f"/api/chat/conversations/{conversation}", data={"mode": "not_a_mode"}
        )
        assert response.status_code == 422

    async def test_forced_mode_skips_routing(self, client, conversation):
        """显式指定模式时应跳过路由（reason 为「用户手动指定」）。"""
        response = await client.post(
            f"/api/chat/conversations/{conversation}/messages",
            json={"content": "解释一下什么是 REST", "mode": "chat"},
        )
        frames = parse_sse(response.text)
        meta = frames[0][1]
        assert meta["mode"] == "chat"
        assert meta["mode_reason"] == "用户手动指定"

    async def test_forced_mode_is_persisted(self, client, conversation):
        await client.post(
            f"/api/chat/conversations/{conversation}/messages",
            json={"content": "解释一下什么是 REST", "mode": "chat"},
        )
        detail = (await client.get(f"/api/chat/conversations/{conversation}")).json()["data"]
        assert detail["conversation"]["mode"] == "chat"


class TestToolResultPersistence:
    """工具结果要以结构化载荷落库，刷新页面后卡片仍可渲染。"""

    async def test_job_cards_saved_as_structured_payload(self, client, conversation):
        from bzp_workbench.models import JobRecord
        from bzp_workbench.db import SessionLocal

        async with SessionLocal() as session:
            session.add(
                JobRecord(
                    job_id="pytest-job-1",
                    title="Python 后端工程师",
                    company="测试公司",
                    salary="20-40K",
                    salary_avg=30,
                    city="深圳",
                    job_link="https://www.zhipin.com/job_detail/pytest-job-1.html",
                    skill_keywords='["Python"]',
                )
            )
            await session.commit()

        try:
            await client.post(
                f"/api/chat/conversations/{conversation}/messages",
                json={"content": "帮我在深圳找 Python 岗位"},
            )
            detail = (await client.get(f"/api/chat/conversations/{conversation}")).json()[
                "data"
            ]
            assistant = detail["messages"][-1]
            assert assistant["content_type"] == "job_cards"
            payload = assistant["content_data"]
            assert payload["payload_type"] == "job_cards"
            jobs = payload["data"]["jobs"]
            assert jobs and jobs[0]["job_link"].endswith(".html")
        finally:
            from sqlalchemy import delete

            async with SessionLocal() as session:
                await session.execute(
                    delete(JobRecord).where(JobRecord.job_id == "pytest-job-1")
                )
                await session.commit()


class TestConversationDeletion:
    async def test_delete_removes_attachment_files_from_disk(self, client, conversation):
        """简历属敏感文件，删除会话必须连带清理磁盘。"""
        from pathlib import Path

        uploaded = await client.post(
            f"/api/chat/conversations/{conversation}/attachments",
            files={"file": ("resume.docx", make_docx(["张三"]), "application/octet-stream")},
        )
        attachment_id = uploaded.json()["data"]["id"]
        detail = (await client.get(f"/api/chat/attachments/{attachment_id}")).json()["data"]

        from bzp_workbench.db import SessionLocal
        from bzp_workbench.models import ChatAttachment

        async with SessionLocal() as session:
            row = await session.get(ChatAttachment, attachment_id)
            stored = Path(row.stored_path)
        assert stored.is_file(), "上传后文件应存在"

        response = await client.delete(f"/api/chat/conversations/{conversation}")
        assert response.status_code == 200
        assert response.json()["data"]["removed_files"] >= 1
        assert not stored.exists(), "删除会话后附件文件必须被清理"
        assert (await client.get(f"/api/chat/conversations/{conversation}")).status_code == 404
