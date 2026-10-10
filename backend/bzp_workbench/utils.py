"""通用工具：时间归一化、JSON 读写、文本清洗。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# 中国标准时间，用于「今日新增」这类面向用户的统计
CST = timezone(timedelta(hours=8))


def utcnow() -> datetime:
    """带时区的当前 UTC 时间。"""
    return datetime.now(timezone.utc)


def to_naive_utc(value: datetime | None) -> datetime | None:
    """统一成 naive UTC，便于和数据库里取出来的值比较。

    SQLite 不保存时区信息，取回来是 naive；PostgreSQL 取回来是 aware。
    两边混着比较会抛 ``TypeError``，所以一律先归一化。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def day_bounds_cst(reference: datetime | None = None) -> tuple[datetime, datetime]:
    """返回「今天（东八区）」的起止时间，以 naive UTC 表示。"""
    now_cst = (reference or utcnow()).astimezone(CST)
    start_cst = now_cst.replace(hour=0, minute=0, second=0, microsecond=0)
    end_cst = start_cst + timedelta(days=1)
    start_utc = start_cst.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_cst.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


def fingerprint(*parts: Any) -> str:
    """稳定指纹，用于目录命名等不含敏感信息的场景。"""
    raw = "\x1f".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def read_json(path: Path | str, default: Any = None) -> Any:
    """容错读取 JSON；文件缺失或损坏时返回默认值。"""
    file_path = Path(path)
    if not file_path.is_file():
        return default
    try:
        with file_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError, ValueError):
        return default


def write_json(path: Path | str, payload: Any) -> None:
    """原子写入 JSON。"""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
    tmp_path.replace(file_path)


def tail_lines(path: Path | str, limit: int = 200) -> tuple[list[str], int]:
    """读取文本文件尾部若干行，返回 (行列表, 总行数)。

    日志文件可能很大，这里只按块读取尾部，避免整文件载入内存。
    """
    file_path = Path(path)
    if not file_path.is_file():
        return [], 0

    block = 8192
    try:
        with file_path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            buffer = b""
            pos = size
            # 最多往回读 256KB，足够覆盖几百行日志
            while pos > 0 and buffer.count(b"\n") <= limit and (size - pos) < 262_144:
                step = min(block, pos)
                pos -= step
                handle.seek(pos)
                buffer = handle.read(step) + buffer
            total_lines = buffer.count(b"\n")
    except OSError:
        return [], 0

    text = buffer.decode("utf-8", errors="replace")
    lines = [line.rstrip("\r") for line in text.splitlines()]
    return lines[-limit:], total_lines


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """去掉终端颜色转义，避免前端日志区出现乱码。"""
    return _ANSI_RE.sub("", text)


def truncate(text: str | None, limit: int = 2000) -> str | None:
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


_WHITESPACE_RE = re.compile(r"[ \t\u3000]+")


def normalize_whitespace(text: str) -> str:
    """压缩空白但保留换行结构（JD 全文排版依赖换行）。"""
    lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()
