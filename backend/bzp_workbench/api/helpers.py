"""API 请求 / 响应辅助。"""

from __future__ import annotations

from typing import Any

from ..schemas import ApiResponse, PageData, PageMeta


def ok(data: Any = None, message: str = "ok") -> ApiResponse:
    return ApiResponse(code=0, message=message, data=data)


def fail(message: str, code: int = 1, data: Any = None) -> ApiResponse:
    return ApiResponse(code=code, message=message, data=data)


def page(items: list[Any], total: int, page_number: int, page_size: int) -> PageData:
    pages = (total + page_size - 1) // page_size if page_size else 0
    return PageData(
        items=items,
        meta=PageMeta(total=total, page=page_number, page_size=page_size, pages=pages),
    )
