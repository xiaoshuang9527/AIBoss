"""对话模块的附件存储与文本抽取。

两条关键约束
------------
1. **按文件内容判断类型，不看扩展名或声明的 MIME**。DeepSeek 官方文档明确
   说图片格式「由文件实际内容判断，而非文件名或声明的 MIME 类型」。用户把
   `resume.png` 改成 `.pdf` 是很常见的事，按扩展名判断会写出错误的 kind。

2. **上传时就抽取文本并缓存**。DeepSeek 规定图片只能出现在 ``user`` 消息里，
   如果多轮对话每轮都把原图重新塞进请求，请求体会迅速膨胀（图片 base64 后
   还要膨胀约 33%，而请求体上限是 48MB）。所以首轮抽取一次，之后只发文本。
"""

from __future__ import annotations

import base64
import mimetypes
import shutil
from dataclasses import dataclass
from pathlib import Path

from ...config import settings

# ---------- 限制（与前端约定一致） ----------
MAX_IMAGE_BYTES = 8 * 1024 * 1024      # 8MB
MAX_DOC_BYTES = 20 * 1024 * 1024       # 20MB
MAX_ATTACHMENTS_PER_MESSAGE = 5

# ---------- 支持的类型 ----------
IMAGE_MIME_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT_MIME_PREFIX = "text/"

# 文本抽取结果的长度上限，避免超长简历把上下文吃满
MAX_EXTRACTED_CHARS = 60_000


class AttachmentError(ValueError):
    """附件相关的用户可读错误。"""


@dataclass
class DetectedFile:
    """按内容嗅探出的文件信息。"""

    kind: str          # image / pdf / docx / text / unsupported
    mime_type: str
    suffix: str


def sniff_file(data: bytes, filename: str) -> DetectedFile:
    """按**文件内容**判断类型（魔数优先，扩展名仅作兜底）。"""
    head = data[:16]

    # ---- 图片魔数 ----
    if head.startswith(b"\xff\xd8\xff"):
        return DetectedFile("image", "image/jpeg", ".jpg")
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return DetectedFile("image", "image/png", ".png")
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return DetectedFile("image", "image/gif", ".gif")
    if head.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return DetectedFile("image", "image/webp", ".webp")

    # ---- PDF ----
    if head.startswith(b"%PDF-"):
        return DetectedFile("pdf", PDF_MIME, ".pdf")

    # ---- DOCX / 其他 OOXML：ZIP 容器，需进一步看内部结构 ----
    if head.startswith(b"PK\x03\x04"):
        if _zip_contains(data, "word/document.xml"):
            return DetectedFile("docx", DOCX_MIME, ".docx")
        # 是压缩包但不是 Word 文档（可能是 xlsx/pptx/普通 zip）
        return DetectedFile("unsupported", "application/zip", ".zip")

    # ---- 纯文本兜底：可解码且无控制字符 ----
    if _looks_like_text(data):
        suffix = Path(filename).suffix.lower() or ".txt"
        guessed, _ = mimetypes.guess_type(filename)
        return DetectedFile("text", guessed or "text/plain", suffix)

    return DetectedFile("unsupported", "application/octet-stream", Path(filename).suffix or "")


def _zip_contains(data: bytes, member: str) -> bool:
    import io
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return member in archive.namelist()
    except (zipfile.BadZipFile, OSError, ValueError):
        return False


def _looks_like_text(data: bytes) -> bool:
    """粗略判断是否为文本：能按 UTF-8 解码，且不含 NUL。"""
    if b"\x00" in data[:4096]:
        return False
    try:
        data[:8192].decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def validate_size(kind: str, size: int) -> None:
    """按类型校验大小，给出明确的上限提示。"""
    if kind == "image":
        if size > MAX_IMAGE_BYTES:
            raise AttachmentError(
                f"图片过大（{_mb(size)}），上限为 {_mb(MAX_IMAGE_BYTES)}"
            )
    elif kind in ("pdf", "docx", "text"):
        if size > MAX_DOC_BYTES:
            raise AttachmentError(
                f"文档过大（{_mb(size)}），上限为 {_mb(MAX_DOC_BYTES)}"
            )
    else:
        raise AttachmentError(
            "不支持的文件类型。可上传 JPEG / PNG / GIF / WebP 图片，"
            "或 PDF / Word / 纯文本文档。"
        )


def _mb(size: int) -> str:
    return f"{size / 1024 / 1024:.1f}MB"


# ============================================================
# 存储位置
# ============================================================
def upload_root() -> Path:
    root = settings.storage_path / "chat-uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def conversation_dir(conversation_id: int) -> Path:
    directory = upload_root() / str(conversation_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def store_file(conversation_id: int, attachment_id: int, data: bytes, suffix: str) -> Path:
    """把附件落盘，返回存储路径。

    文件名用 ``附件 id + 后缀``，不采用用户提供的文件名 ——
    避免路径穿越（``../../``）与重名覆盖。
    """
    target = conversation_dir(conversation_id) / f"{attachment_id}{suffix}"
    target.write_bytes(data)
    return target


def delete_conversation_files(conversation_id: int) -> int:
    """删除整个会话的附件目录，返回删除的文件数。

    删除会话时必须调用 —— 简历属于敏感文件，不能留在磁盘上。
    """
    directory = upload_root() / str(conversation_id)
    if not directory.is_dir():
        return 0
    count = sum(1 for item in directory.rglob("*") if item.is_file())
    shutil.rmtree(directory, ignore_errors=True)
    return count


# ============================================================
# 文本抽取
# ============================================================
def extract_text(path: Path, kind: str) -> tuple[str, str]:
    """抽取文本，返回 ``(text, status)``。

    ``status`` 取值：``done`` / ``not_applicable`` / ``failed``。
    图片返回 ``not_applicable`` —— 它的内容由视觉模型直接理解，
    不做本地 OCR（会丢版式信息）。
    """
    try:
        if kind == "text":
            return _truncate(_read_text(path)), "done"
        if kind == "pdf":
            return _truncate(_read_pdf(path)), "done"
        if kind == "docx":
            return _truncate(_read_docx(path)), "done"
        if kind == "image":
            return "", "not_applicable"
        return "", "not_applicable"
    except Exception as exc:  # noqa: BLE001 - 抽取失败不应让上传失败
        return "", f"failed:{type(exc).__name__}: {exc}"


def _truncate(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) > MAX_EXTRACTED_CHARS:
        return cleaned[:MAX_EXTRACTED_CHARS] + "\n…（内容过长已截断）"
    return cleaned


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "gb18030", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_pdf(path: Path) -> str:
    """PDF 文本抽取。缺少 pdfplumber 时给出可读提示而不是崩溃。"""
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("缺少 pdfplumber，请在后端环境安装后重试") from exc

    pages: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(f"--- 第 {index} 页 ---\n{text}")
    return "\n\n".join(pages)


def _read_docx(path: Path) -> str:
    """Word 文本抽取：段落 + 表格。"""
    try:
        import docx  # python-docx
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("缺少 python-docx，请在后端环境安装后重试") from exc

    document = docx.Document(str(path))
    blocks: list[str] = [para.text for para in document.paragraphs if para.text.strip()]

    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                blocks.append(" | ".join(cells))

    return "\n".join(blocks)


# ============================================================
# 供模型使用
# ============================================================
def to_image_part(path: Path, mime_type: str):
    """把图片读成适配器认识的 ImagePart。

    图片进入请求时要转 base64，这一步是 DeepSeek 视觉输入的要求。

    注意 import 层级：``ai`` 在 ``services`` 下，``llm`` 是它的兄弟包，
    所以要用 ``..llm``。写成 ``.llm`` 会去找 ``ai.llm`` 并报
    ``No module named 'bzp_workbench.services.ai.llm'`` —— 表现为
    「上传图片后一发消息就失败」。
    """
    from ..llm import ImagePart

    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return ImagePart(data=data, mime_type=mime_type)


def build_attachment_context(attachments: list) -> str:
    """把附件汇总成一段可注入 prompt 的文本。

    图片不在这里处理（它走多模态输入），只列出文件名提示模型"用户还上传了图片"。
    """
    if not attachments:
        return ""

    lines: list[str] = ["【用户上传的附件】"]
    for item in attachments:
        if item.kind == "image":
            lines.append(f"- 图片：{item.filename}（内容见随附的图像输入）")
        elif item.extracted_text:
            lines.append(
                f"- 文档：{item.filename}\n"
                f"  抽取内容如下：\n{item.extracted_text}"
            )
        else:
            lines.append(f"- 文档：{item.filename}（未能抽取文本内容）")
    return "\n".join(lines)


__all__ = [
    "AttachmentError",
    "MAX_ATTACHMENTS_PER_MESSAGE",
    "MAX_DOC_BYTES",
    "MAX_IMAGE_BYTES",
    "build_attachment_context",
    "conversation_dir",
    "delete_conversation_files",
    "extract_text",
    "sniff_file",
    "store_file",
    "to_image_part",
    "upload_root",
    "validate_size",
]
