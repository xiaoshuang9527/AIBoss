"""工具：发起 BOSS 直聘抓取任务。

为什么这个工具需要那么多"礼貌的约束"
------------------------------------
这是一个**有副作用的**工具：它会在后台真的跑一个 10-20 分钟的抓取进程。
和 `search_existing_jobs`（只读、毫秒级）完全不同，所以必须：

* **强制去重**：同一个关键词+城市+筛选条件在短时间内重复提交，只会拿到
  重复数据，还白白触发风控。这里在提交前先查"是否已有相同条件的活跃任务"。
* **限制页数**：模型很容易说"我来抓 50 页给你"，那要跑几小时。硬上限兜住。
* **检查登录态**：没登录就抓，任务必然失败，不如当场告诉用户去授权。
* **明确告知耗时**：用户点完发现要等 15 分钟却没被告知，体验很差。
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from ....enums import LogCategory, TaskStatus
from ....models import ScrapeTask
from ....utils import utcnow
from ... import repository as repo
from ...auth_service import get_default_or_create
from ...logging_service import log_event
from ...queue_service import task_queue
from ..salary import SalaryResolution, resolve_salary
from .base import Tool, ToolContext, ToolError, ToolResult
from .search_jobs import _normalize_city

#: 单次任务的页数硬上限。
#:
#: 页数 → 耗时大致线性：3 页约 3-5 分钟，10 页约 10-15 分钟。
#: 允许模型设更大值会让用户以为"点一下就好"，实际要等很久。
MAX_PAGES = 10
DEFAULT_PAGES = 3

#: 判定"重复任务"的时间窗（秒）。窗口内相同条件的活跃任务不重复创建。
DEDUPE_WINDOW_SECONDS = 30 * 60

#: 仍算"活跃"的任务状态。
#: 注意没有 RETRYING —— 重试中的任务状态仍是 RUNNING，重试次数记在 attempts 里。
_ACTIVE_STATUSES = (
    TaskStatus.PENDING.value,
    TaskStatus.RUNNING.value,
)

#: 经验要求 → 爬虫档位码
_EXPERIENCE_MAP: dict[str, str] = {
    "不限": "0",
    "应届生": "101",
    "在校生": "102",
    "1年以内": "103",
    "1-3年": "104",
    "3-5年": "105",
    "5-10年": "106",
    "10年以上": "107",
}

#: 学历要求 → 爬虫档位码
_DEGREE_MAP: dict[str, str] = {
    "不限": "0",
    "初中及以下": "201",
    "中专/中技": "202",
    "高中": "203",
    "大专": "204",
    "本科": "205",
    "硕士": "206",
    "博士": "207",
}


class TriggerBossScraperTool(Tool):
    name = "trigger_boss_scraper"
    description = (
        "发起一次真实的 BOSS 直聘抓取任务，把新岗位写入本地库。"
        "**耗时 10-20 分钟**，在后台异步执行。"
        "只在本地库检索不到所需岗位时才使用；能查到就别抓。"
        "发起前必须已确认关键词与城市；缺少任一必填项时应先向用户确认。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "岗位关键词，如 Python、前端、产品经理。必填",
            },
            "city": {
                "type": "string",
                "description": "城市中文名，如 深圳、北京。必填",
            },
            "salary": {
                "type": "string",
                "description": (
                    "薪资要求，如 15-25K、20K以上。选填。"
                    "注意 BOSS 只有粗档位筛选，写入区间时会说明实际档位"
                ),
            },
            "experience": {
                "type": "string",
                "description": "经验要求，如 1-3年、3-5年、不限。选填",
            },
            "degree": {
                "type": "string",
                "description": "学历要求，如 大专、本科、硕士、不限。选填",
            },
            "max_pages": {
                "type": "integer",
                "description": f"抓取页数上限，默认 {DEFAULT_PAGES}，最大 {MAX_PAGES}。每页约 30 条",
            },
            "with_detail": {
                "type": "boolean",
                "description": "是否抓取岗位详情（JD 全文）。默认 true，会更慢但信息完整",
            },
            "note": {
                "type": "string",
                "description": "任务备注，便于用户日后在历史记录里辨认，如「AI 方向」",
            },
        },
        "required": ["keyword", "city"],
    }
    modes = frozenset({"job_hunting"})

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        started = time.monotonic()

        keyword = self.get_str(arguments, "keyword")
        city = self.get_str(arguments, "city")
        if not keyword or not city:
            raise ToolError(
                "关键词与城市都是必填项",
                hint="先向用户确认缺少的那一项，不要自己假设一个城市或方向。",
            )

        normalized_city = _normalize_city(city)
        salary_raw = self.get_str(arguments, "salary") or None
        experience = self._pick_option(arguments, "experience", _EXPERIENCE_MAP)
        degree = self._pick_option(arguments, "degree", _DEGREE_MAP)

        max_pages = self.get_int(arguments, "max_pages", DEFAULT_PAGES) or DEFAULT_PAGES
        capped = max_pages > MAX_PAGES
        max_pages = max(1, min(max_pages, MAX_PAGES))
        with_detail = bool(arguments.get("with_detail", True))
        note = self.get_str(arguments, "note") or None

        # ---- 登录态检查：没登录就不要浪费用户 20 分钟 ----
        profile = await get_default_or_create(context.db)
        if profile is None:
            raise ToolError(
                "还没有可用的授权档案",
                hint="请到「账号授权」页完成一次扫码登录后再发起抓取。",
            )
        if profile.status != "active":
            raise ToolError(
                f"BOSS 直聘登录态不可用（当前状态：{profile.status}）",
                hint=(
                    "请到「账号授权」页点「开始授权」重新扫码登录，"
                    "然后回来再发起抓取。未登录时抓取一定会失败。"
                ),
            )

        salary = resolve_salary(salary_raw)

        # ---- 去重：相同条件的活跃任务不重复创建 ----
        duplicate = await self._find_active_duplicate(context.db, keyword, normalized_city)
        if duplicate is not None:
            return self._duplicate_result(duplicate, salary, started)

        task = await repo.create_task(
            context.db,
            {
                "keyword": keyword,
                "city": normalized_city,
                "salary": salary.code if not salary.is_unlimited else None,
                "experience": experience,
                "degree": degree,
                "max_pages": max_pages,
                "with_detail": with_detail,
                "note": note or f"AI 对话发起（会话 #{context.conversation_id}）",
                "auth_profile_id": profile.id,
                # 记录发起它的对话，后台进度才能推回这里（P3）
                "conversation_id": context.conversation_id,
                "status": TaskStatus.PENDING.value,
                "stage_text": "已入队，等待执行",
            },
        )

        await log_event(
            context.db,
            f"AI 对话发起抓取任务 #{task.id}：{keyword} @ {normalized_city}"
            f"（{max_pages} 页，详情={'是' if with_detail else '否'}）",
            category=LogCategory.TASK,
            task_id=task.id,
        )
        task_queue.notify()

        payload = {
            "task_id": task.id,
            "status": task.status,
            "status_label": "待处理",
            "keyword": keyword,
            "city": normalized_city,
            "salary": salary.to_payload(),
            "experience": experience,
            "degree": degree,
            "max_pages": max_pages,
            "with_detail": with_detail,
            # 粗略估计：每页约 1 分钟（含详情抓取则更久）
            "estimated_minutes": max(3, max_pages * (2 if with_detail else 1)),
        }

        return ToolResult(
            payload_type="task_created",
            data=payload,
            model_text=self._model_text(payload, salary, capped, duplicate=False),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @staticmethod
    def _pick_option(
        arguments: dict[str, Any], key: str, mapping: dict[str, str]
    ) -> str | None:
        """把用户写的选项映射成档位码；"不限" 归一化为 None（=不传该筛选）。"""
        raw = Tool.get_str(arguments, key)
        if not raw:
            return None
        if raw in mapping:
            code = mapping[raw]
            return None if code == "0" else code
        # 允许模型直接传码
        if raw.isdigit() and raw in mapping.values():
            return None if raw == "0" else raw
        # 允许 "3-5年经验" 这类带后缀的写法
        for label, code in mapping.items():
            if label != "不限" and label in raw:
                return code
        return None

    @staticmethod
    async def _find_active_duplicate(
        db: Any, keyword: str, city: str
    ) -> ScrapeTask | None:
        """查最近是否有相同关键词+城市的活跃任务。"""
        cutoff = utcnow() - timedelta(seconds=DEDUPE_WINDOW_SECONDS)
        rows = (
            (
                await db.execute(
                    select(ScrapeTask)
                    .where(
                        ScrapeTask.keyword == keyword,
                        ScrapeTask.city == city,
                        ScrapeTask.status.in_(_ACTIVE_STATUSES),
                        ScrapeTask.created_at >= cutoff,
                    )
                    .order_by(ScrapeTask.id.desc())
                    .limit(1)
                )
            )
            .scalars()
            .all()
        )
        return rows[0] if rows else None

    @staticmethod
    def _duplicate_result(
        task: ScrapeTask, salary: Any, started: float
    ) -> ToolResult:
        payload = {
            "task_id": task.id,
            "status": task.status,
            "status_label": "进行中（已存在相同任务）",
            "keyword": task.keyword,
            "city": task.city,
            "salary": salary.to_payload(),
            "max_pages": task.max_pages,
            "with_detail": task.with_detail,
            "estimated_minutes": max(3, (task.max_pages or 3) * 2),
            "duplicate": True,
        }
        return ToolResult(
            payload_type="task_created",
            data=payload,
            model_text=(
                f"已经有一个相同条件的任务在进行中（任务 #{task.id}："
                f"{task.keyword} @ {task.city}，状态 {task.status}）。"
                "**没有重复创建**。请告诉用户等待这个任务完成即可，"
                "不要让他以为发起了两个任务。"
            ),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    @staticmethod
    def _model_text(
        payload: dict[str, Any], salary: Any, capped: bool, *, duplicate: bool
    ) -> str:
        lines = [
            f"抓取任务已创建：任务 #{payload['task_id']}",
            f"关键词：{payload['keyword']}　城市：{payload['city']}",
            f"抓取页数：{payload['max_pages']} 页（约 {payload['estimated_minutes']} 分钟）",
            f"是否抓详情：{'是' if payload['with_detail'] else '否'}",
            salary.to_model_text(),
        ]
        if capped:
            lines.append(
                f"注意：用户要求的页数超过上限，已自动收紧到 {payload['max_pages']} 页。"
                "请如实告知这一点。"
            )
        lines.append(
            "任务在后台异步执行，界面会显示任务卡片，用户可到「历史记录」页查看进度。"
            "你现在应该：告诉用户任务已开始、大致需要多久、"
            "并说明完成后可以再来让你检索结果。"
            "**不要**声称你已经拿到了新岗位数据 —— 任务还没跑完。"
        )
        return "\n".join(lines)


__all__ = ["MAX_PAGES", "TriggerBossScraperTool"]
