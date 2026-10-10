"""导出为 CSV / JSON。

CSV 带 UTF-8 BOM，保证 Excel 直接双击打开不乱码。
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Iterable

from ..models import JobRecord

# CSV 表头（含岗位详情全文，满足「导出后可直接阅读」的需求）
CSV_COLUMNS: tuple[tuple[str, str], ...] = (
    ("岗位名称", "title"),
    ("公司名称", "company"),
    ("薪资范围", "salary"),
    ("月薪下限(K)", "salary_min"),
    ("月薪上限(K)", "salary_max"),
    ("月薪均值(K)", "salary_avg"),
    ("经验要求", "experience"),
    ("学历要求", "degree"),
    ("城市", "city"),
    ("区域", "location"),
    ("公司规模", "company_scale"),
    ("融资阶段", "company_stage"),
    ("所属行业", "company_industry"),
    ("招聘者", "boss_name"),
    ("招聘者职位", "boss_title"),
    ("活跃状态", "boss_active_status"),
    ("技能标签", "skill_keywords_flat"),
    ("福利待遇", "welfare"),
    ("岗位职责", "job_duty"),
    ("岗位要求", "job_requirement"),
    ("岗位详情全文", "jd"),
    ("原始链接", "job_link"),
    ("公司主页", "company_link"),
    ("是否新增", "is_new"),
    ("抓取时间", "scraped_at"),
)


def _json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if isinstance(parsed, list):
        return [str(item) for item in parsed if str(item).strip()]
    return []


def job_to_row(job: JobRecord, include_jd: bool = True) -> dict[str, Any]:
    """把 ORM 对象摊平成一行（列名用中文，导出即用）。"""
    row: dict[str, Any] = {}
    skills = ", ".join(_json_list(job.skill_keywords)) or (job.tags or "")

    for header, attribute in CSV_COLUMNS:
        if not include_jd and attribute in ("job_duty", "job_requirement", "jd"):
            continue
        if attribute == "skill_keywords_flat":
            row[header] = skills
            continue
        value = getattr(job, attribute, None)
        if attribute == "is_new":
            row[header] = "是" if value else "否"
            continue
        if hasattr(value, "strftime"):
            row[header] = value.strftime("%Y-%m-%d %H:%M:%S")
            continue
        row[header] = "" if value is None else value
    return row


def jobs_to_csv(jobs: Iterable[JobRecord], include_jd: bool = True) -> str:
    """生成 CSV 文本（含 BOM）。"""
    buffer = io.StringIO()
    headers = [
        header
        for header, attribute in CSV_COLUMNS
        if include_jd or attribute not in ("job_duty", "job_requirement", "jd")
    ]
    writer = csv.DictWriter(buffer, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for job in jobs:
        writer.writerow(job_to_row(job, include_jd=include_jd))
    # \ufeff 让 Excel 正确识别 UTF-8
    return "\ufeff" + buffer.getvalue()


def jobs_to_json(jobs: Iterable[JobRecord], include_jd: bool = True) -> str:
    """生成 JSON 文本。"""
    payload = []
    for job in jobs:
        row = job_to_row(job, include_jd=include_jd)
        # JSON 里额外保留结构化字段，方便二次处理
        row["skill_tags"] = _json_list(job.skill_tags)
        row["skill_keywords"] = _json_list(job.skill_keywords)
        row["job_id"] = job.job_id
        payload.append(row)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_filename(task_name: str, fmt: str) -> str:
    """构造导出文件名。"""
    import re
    import time

    safe = re.sub(r"[^\w\u4e00-\u9fa5-]+", "_", task_name).strip("_") or "jobs"
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return f"{safe}_{stamp}.{fmt}"
