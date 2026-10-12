"""工具：检索本地岗位库。

为什么这个工具优先于抓取
------------------------
抓取一个任务要 10-20 分钟，而本地库查询是毫秒级。用户问「深圳的 Python 岗位」时，
绝大多数情况下库里已经有数据（工作台一直在累积），所以**先查库、查不到再提示抓取**
是这个工作台的核心策略。

返回值同时服务两个消费者：
* ``model_text`` —— 给模型看的紧凑 JSON，它据此组织语言
* ``data``       —— 给前端渲染的岗位卡片（含「去 BOSS 沟通」链接）
"""

from __future__ import annotations

import time
from typing import Any

from ... import repository as repo
from ..job_format import build_job_cards, build_job_model_text
from .base import Tool, ToolContext, ToolError, ToolResult

#: 单次最多返回的岗位数（太多会撑爆上下文，也让界面难读）
MAX_RESULTS = 10
DEFAULT_RESULTS = 5


class SearchExistingJobsTool(Tool):
    name = "search_existing_jobs"
    description = (
        "从本地已抓取的岗位库中检索岗位。这是**首选**的查询岗位方式，"
        "毫秒级返回，不消耗抓取额度。"
        "如果返回 0 条，说明库里还没有匹配数据，应改用 trigger_boss_scraper 发起抓取。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "岗位关键词，如 Python、前端、产品经理、AI Agent",
            },
            "city": {
                "type": "string",
                "description": "城市中文名，如 深圳、北京、上海。留空表示不限城市",
            },
            "salary_min": {
                "type": "integer",
                "description": "期望月薪下限（单位：千元），如 15 表示 15K",
            },
            "salary_max": {
                "type": "integer",
                "description": "期望月薪上限（单位：千元），如 25 表示 25K",
            },
            "limit": {
                "type": "integer",
                "description": f"返回条数，默认 {DEFAULT_RESULTS}，最多 {MAX_RESULTS}",
            },
        },
        "required": [],
    }
    modes = frozenset({"job_hunting", "chat"})

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        started = time.monotonic()

        keyword = self.get_str(arguments, "keyword") or None
        city = self.get_str(arguments, "city") or None
        salary_min = self.get_int(arguments, "salary_min")
        salary_max = self.get_int(arguments, "salary_max")
        limit = self.get_int(arguments, "limit", DEFAULT_RESULTS) or DEFAULT_RESULTS
        limit = max(1, min(limit, MAX_RESULTS))

        if not keyword and not city:
            raise ToolError(
                "关键词与城市至少要提供一个",
                hint="如果用户还没说明求职方向或城市，先向用户确认后再查询。",
            )

        # 城市名做一次宽松匹配：库里存的是「深圳」，用户可能说「深圳市」
        normalized_city = _normalize_city(city) if city else None

        try:
            rows, total = await repo.list_jobs(
                context.db,
                page=1,
                page_size=limit,
                order_by="salary_desc",
                keyword=keyword,
                city=normalized_city,
                salary_min=salary_min,
                salary_max=salary_max,
            )
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"查询岗位库失败：{type(exc).__name__}: {exc}") from exc

        if not rows:
            # 不要当作错误抛：让模型看到"0 条"并自行决定是否发起抓取
            return ToolResult(
                payload_type="job_cards",
                data={
                    "jobs": [],
                    "total": 0,
                    "query": {"keyword": keyword, "city": city},
                },
                model_text=(
                    f"本地岗位库中没有匹配的岗位"
                    f"（关键词={keyword or '不限'}，城市={city or '不限'}，"
                    f"薪资={salary_min or '不限'}-{salary_max or '不限'}K）。"
                    "\n库里已有数据不等于覆盖所有城市/关键词。"
                    "如果用户想要更多结果，可以调用 trigger_boss_scraper 发起一次真实抓取。"
                ),
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        cards = build_job_cards(rows)
        return ToolResult(
            payload_type="job_cards",
            data={
                "jobs": cards,
                "total": total,
                "returned": len(cards),
                "query": {
                    "keyword": keyword,
                    "city": city,
                    "salary_min": salary_min,
                    "salary_max": salary_max,
                },
            },
            model_text=build_job_model_text(cards, total,
                                            keyword=keyword, city=city),
            latency_ms=int((time.monotonic() - started) * 1000),
        )


def _normalize_city(city: str) -> str:
    """把「深圳市」「深圳 」统一成「深圳」，与库里的存法对齐。

    库里 ``JobRecord.city`` 存的是爬虫给的 cityName（如「深圳」），
    用户口语里常带「市」或空格，不做归一化会查不到。
    """
    cleaned = city.strip()
    for suffix in ("市", "特别行政区", "自治区"):
        if cleaned.endswith(suffix) and len(cleaned) > len(suffix):
            cleaned = cleaned[: -len(suffix)]
    return cleaned or city


__all__ = ["SearchExistingJobsTool"]
