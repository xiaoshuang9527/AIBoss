"""工具：查询抓取任务的进度。

为什么需要它
------------
抓取要 10-20 分钟，用户自然会问「好了吗」。如果没有这个工具，模型要么
编一个进度（严重违反"禁止编造数据"），要么只能说"我不知道"。

有了它，模型可以给出真实状态；同时它也是**唯一的复核手段** ——
用户说"我发起的任务呢"时，模型能用它确认真实存在的任务，而不是凭记忆。
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select

from ....enums import TaskStatus
from ....models import ScrapeTask
from .base import Tool, ToolContext, ToolResult

#: 未结束的状态（重试中的任务状态仍是 running，重试次数记在 attempts 里）
_ACTIVE = {TaskStatus.PENDING.value, TaskStatus.RUNNING.value}


def _status_label(status: str) -> str:
    """状态 → 中文标签。

    直接复用枚举自带的 label，避免在这里再维护一份映射 ——
    两份映射一旦不同步，界面和历史记录页会对同一状态显示不同说法。
    """
    try:
        return TaskStatus(status).label
    except ValueError:
        return status


class CheckTaskStatusTool(Tool):
    name = "check_task_status"
    description = (
        "查询抓取任务的当前进度与结果。"
        "用户问「任务好了吗」「抓了多少」时使用。"
        "不传 task_id 时返回最近几个任务。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "integer",
                "description": "任务编号。用户明确提到某个任务时传入，否则省略",
            },
            "limit": {
                "type": "integer",
                "description": "不指定 task_id 时返回的条数，默认 3，最多 10",
            },
        },
        "required": [],
    }
    modes = frozenset({"job_hunting"})

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        started = time.monotonic()
        task_id = self.get_int(arguments, "task_id")

        if task_id is not None:
            task = await context.db.get(ScrapeTask, task_id)
            if task is None:
                return ToolResult(
                    payload_type="text",
                    data=None,
                    model_text=(
                        f"没有找到编号为 {task_id} 的任务。"
                        "请让用户确认任务编号，或调用本工具不带参数查看最近任务。"
                    ),
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
            tasks = [task]
        else:
            limit = self.get_int(arguments, "limit", 3) or 3
            limit = max(1, min(limit, 10))
            tasks = list(
                (
                    await context.db.execute(
                        select(ScrapeTask).order_by(ScrapeTask.id.desc()).limit(limit)
                    )
                )
                .scalars()
                .all()
            )

        if not tasks:
            return ToolResult(
                payload_type="task_status",
                data={"tasks": []},
                model_text=(
                    "本地还没有任何抓取任务。"
                    "如果用户想要岗位数据，可以调用 trigger_boss_scraper 发起一次。"
                ),
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        payloads = [self._to_payload(task) for task in tasks]
        return ToolResult(
            payload_type="task_status",
            data={"tasks": payloads},
            model_text=self._model_text(payloads),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _to_payload(task: ScrapeTask) -> dict[str, Any]:
        return {
            "task_id": task.id,
            "keyword": task.keyword,
            "city": task.city,
            "status": task.status,
            "status_label": _status_label(task.status),
            "is_active": task.status in _ACTIVE,
            "progress": task.progress or 0,
            "stage_text": task.stage_text,
            "total_found": task.total_found or 0,
            "new_count": task.new_count or 0,
            "detail_count": task.detail_count or 0,
            "error_message": task.error_message,
            "duration_seconds": task.duration_seconds,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        }

    @staticmethod
    def _model_text(payloads: list[dict[str, Any]]) -> str:
        lines = ["抓取任务状态：", ""]
        for item in payloads:
            lines.append(
                f"#{item['task_id']} {item['keyword']} @ {item['city']} — "
                f"{item['status_label']}"
            )
            if item["is_active"]:
                lines.append(
                    f"   进度 {item['progress']}%"
                    + (f"　当前阶段：{item['stage_text']}" if item["stage_text"] else "")
                )
            else:
                lines.append(
                    f"   抓到 {item['total_found']} 条，其中新增 {item['new_count']} 条"
                    + (f"，含 {item['detail_count']} 条详情" if item["detail_count"] else "")
                )
            if item["error_message"]:
                lines.append(f"   错误：{item['error_message']}")

        if any(item["is_active"] for item in payloads):
            lines.append("")
            lines.append(
                "有任务仍在进行中。请告诉用户大概还要等多久，并说明"
                "**完成后可以再用 search_existing_jobs 检索新数据**。"
                "不要在没有新数据前就说已经抓到了岗位。"
            )
        return "\n".join(lines)


__all__ = ["CheckTaskStatusTool"]
