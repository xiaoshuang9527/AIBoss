"""工具：读取某个岗位的完整 JD。

为什么需要它
------------
``search_existing_jobs`` 返回的文本里 JD 被压到 60 字 —— 那是为了控制上下文
体积（给模型看的文本会进入对话历史、每轮重发）。但用户经常需要看全文：

* 「这个岗位具体要求什么？」→ 需要完整 JD
* 「帮我分析这个岗位适不适合我」→ 需要职责与要求原文
* 「面试会问什么」→ 需要 JD 里的技术栈细节

如果没有这个工具，模型只有两种选择：基于 60 字摘要硬编（容易跑偏），
或者承认看不到（用户就得自己去岗位结果页翻）。所以它是一个
**按需拉取**的补充：摘要常驻上下文，全文只在需要时取一次。
"""

from __future__ import annotations

import time
from typing import Any

from ....models import JobRecord
from ... import repository as repo
from .base import Tool, ToolContext, ToolError, ToolResult

#: 单次返回给模型的 JD 最大字符数。
#:
#: JD 全文常见 1000-3000 字。取全文会让上下文迅速膨胀，而 4000 字已经
#: 足够覆盖绝大多数 JD 的职责与要求部分。
MAX_JD_CHARS = 4000


class GetJobDetailTool(Tool):
    name = "get_job_detail"
    description = (
        "读取某个岗位的完整 JD（职责与要求原文）。"
        "当你需要分析岗位具体要求、评估匹配度、或准备面试问题时使用。"
        "可以用 local_id（检索结果里的 id）或 job_id（BOSS 的岗位编号）指定。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "local_id": {
                "type": "integer",
                "description": "岗位在本地的编号（search_existing_jobs 结果里的 id 字段）",
            },
            "job_id": {
                "type": "string",
                "description": "BOSS 直聘的岗位编号（结果里的 job_id 字段）",
            },
        },
        "required": [],
    }
    modes = frozenset({"job_hunting", "interview_coach", "chat"})

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        started = time.monotonic()

        local_id = self.get_int(arguments, "local_id")
        biz_id = self.get_str(arguments, "job_id")

        if local_id is None and not biz_id:
            raise ToolError(
                "需要提供 local_id 或 job_id",
                hint=(
                    "先用 search_existing_jobs 检索，从结果里取一个岗位的 id，"
                    "再调用本工具读取它的完整 JD。"
                ),
            )

        job: JobRecord | None = None
        try:
            if local_id is not None:
                job = await repo.get_job(context.db, local_id)
            else:
                job = await repo.get_job_by_biz_id(context.db, biz_id)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"查询岗位失败：{type(exc).__name__}: {exc}") from exc

        if job is None:
            target = f"local_id={local_id}" if local_id is not None else f"job_id={biz_id}"
            return ToolResult(
                payload_type="text",
                data=None,
                model_text=(
                    f"没有找到对应的岗位（{target}）。"
                    "可能编号写错了，或该岗位已被删除。"
                    "请重新调用 search_existing_jobs 确认编号。"
                ),
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        # JD 优先用结构化字段；抓取详情失败时 jd 字段可能为空
        jd_text = (job.jd or "").strip()
        duty = (job.job_duty or "").strip()
        requirement = (job.job_requirement or "").strip()

        if not jd_text and not duty and not requirement:
            return ToolResult(
                payload_type="job_detail",
                data=self._to_payload(
                    job,
                    combined="",
                    duty="",
                    requirement="",
                    truncated=False,
                ),
                model_text=(
                    f"岗位「{job.title}」（{job.company}）**还没有抓取 JD 详情**。"
                    "可能是该任务创建时关闭了「抓取详情」，或详情抓取失败了。\n"
                    "你可以：① 基于标题、技能标签、薪资等信息做初步分析，"
                    "并明确告诉用户没有 JD 全文；"
                    "② 或建议用户重新发起一次开启详情的抓取。\n"
                    "**不要**凭标题编造岗位职责。"
                ),
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        # 截断只作用一次，然后**同时**用于给模型的文本和给前端的 payload。
        # 早先只截断了 model_text，payload 里仍是全文 —— 前端卡片会显示
        # 超长 JD，把一条消息撑到几屏高。
        truncated = False
        combined = "\n\n".join(
            part for part in (duty, requirement, jd_text) if part
        )
        if len(combined) > MAX_JD_CHARS:
            combined = combined[:MAX_JD_CHARS]
            truncated = True

        payload = self._to_payload(
            job,
            combined=combined,
            duty=duty,
            requirement=requirement,
            truncated=truncated,
        )
        return ToolResult(
            payload_type="job_detail",
            data=payload,
            model_text=self._model_text(job, combined, truncated),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _to_payload(
        job: JobRecord,
        *,
        combined: str,
        duty: str,
        requirement: str,
        truncated: bool,
    ) -> dict[str, Any]:
        """构造给前端的 payload。

        ``combined`` 是**已经截断过的**全文（职责 + 要求 + jd 拼接），
        截断后原字段会超出上限，所以这里用 ``combined`` 回填 ``jd`` ——
        前端拿到的文本与给模型的文本一致，不会出现"模型看到的和用户看到的
        不一样"这种困惑。
        """
        return {
            "id": job.id,
            "job_id": job.job_id,
            "title": job.title,
            "company": job.company,
            "salary": job.salary or "面议",
            "city": job.city,
            "location": job.location,
            "experience": job.experience,
            "degree": job.degree,
            "company_scale": job.company_scale,
            "company_stage": job.company_stage,
            "company_industry": job.company_industry,
            "boss_name": job.boss_name,
            "boss_active_status": job.boss_active_status,
            "welfare": job.welfare,
            # 结构化字段保留原值便于前端分区展示；截断时由 jd 兜底
            "job_duty": (None if truncated else duty) or None,
            "job_requirement": (None if truncated else requirement) or None,
            "jd": combined or None,
            "job_link": job.job_link,
            "has_detail": bool(combined),
            "truncated": truncated,
        }

    @staticmethod
    def _model_text(job: JobRecord, combined: str, truncated: bool) -> str:
        lines = [
            f"岗位详情：{job.title} | {job.company} | {job.salary or '面议'}",
            f"地点：{job.location or job.city or '未知'}"
            f"　经验：{job.experience or '不限'}　学历：{job.degree or '不限'}",
        ]
        if job.welfare:
            lines.append(f"福利：{job.welfare}")
        lines.append("")
        lines.append("JD 原文：")
        lines.append(combined)
        if truncated:
            lines.append(
                f"\n（JD 过长，已截断到 {MAX_JD_CHARS} 字。"
                "如果需要后面的部分，请告诉用户去岗位结果页查看完整版本。）"
            )
        if job.job_link:
            lines.append(f"\n原始链接：{job.job_link}")
        lines.append(
            "\n引用链接时必须用上面这个，不要编造。"
            "分析匹配度时只依据 JD 原文，不要补充 JD 里没提到的要求。"
        )
        return "\n".join(lines)


__all__ = ["GetJobDetailTool", "MAX_JD_CHARS"]
