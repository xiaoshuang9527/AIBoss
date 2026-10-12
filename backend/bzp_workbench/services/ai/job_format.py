"""岗位数据的两种表示：给模型看的文本、给前端渲染的卡片。

为什么需要这一层
----------------
同一个岗位列表有两个消费者，需求完全不同：

* **模型**需要紧凑、信息密度高的文本，且必须**包含跳转链接**才能回答
  「去 BOSS 沟通」这类问题；
* **前端**需要结构化数据来渲染可点击的卡片。

混在一起写会让两边都别扭，所以显式分开。
"""

from __future__ import annotations

import json
from typing import Any

from ...models import JobRecord
from ...parsing import salary_bucket

#: 卡片里 JD 摘要的长度上限
JD_PREVIEW_CHARS = 120


def _load_skills(raw: str | None, limit: int = 6) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()][:limit]


def build_job_cards(rows: list[JobRecord]) -> list[dict[str, Any]]:
    """转成前端岗位卡片结构。

    字段与「岗位结果」页保持一致，便于复用岗位卡片的视觉与跳转逻辑。
    """
    cards: list[dict[str, Any]] = []
    for row in rows:
        cards.append(
            {
                "id": row.id,
                "job_id": row.job_id,
                "title": row.title,
                "company": row.company,
                "salary": row.salary or "面议",
                "salary_avg": row.salary_avg,
                "salary_bucket": salary_bucket(row.salary_avg),
                "city": row.city,
                "location": row.location,
                "experience": row.experience,
                "degree": row.degree,
                "company_scale": row.company_scale,
                "company_stage": row.company_stage,
                "company_industry": row.company_industry,
                "boss_name": row.boss_name,
                "boss_active_status": row.boss_active_status,
                "skills": _load_skills(row.skill_keywords) or _load_skills(row.skill_tags),
                "welfare": row.welfare,
                "jd_preview": (row.jd or "")[:JD_PREVIEW_CHARS] or None,
                "has_jd": bool(row.jd),
                # 「去 BOSS 沟通」按钮的目标地址
                "job_link": row.job_link,
                "company_link": row.company_link,
                "is_favorite": row.is_favorite,
                "scraped_at": row.scraped_at.isoformat() if row.scraped_at else None,
            }
        )
    return cards


def build_job_model_text(
    cards: list[dict[str, Any]],
    total: int,
    *,
    keyword: str | None = None,
    city: str | None = None,
) -> str:
    """给模型看的紧凑文本。

    设计权衡（实测踩过坑）
    ----------------------
    这段文本会进入对话历史，**每一轮都要重发**。第一版把完整字段全部展开，
    导致一次追问的 prompt 从 1971 涨到 5063 tokens，首字延迟飙到 11 秒。

    所以这里只保留模型组织语言必需的字段，JD 摘要压到 60 字以内；
    完整信息由前端卡片渲染，模型不需要复述。

    但 ``job_link`` 必须保留：用户问「给我链接」时模型要能直接给出，
    否则会编造 URL —— 这是最容易忽视的幻觉来源。
    """
    header_parts = []
    if keyword:
        header_parts.append(f"关键词={keyword}")
    if city:
        header_parts.append(f"城市={city}")
    header = "、".join(header_parts) or "不限条件"

    lines = [f"岗位库检索（{header}）：命中 {total} 条，返回 {len(cards)} 条。", ""]

    for index, card in enumerate(cards, start=1):
        # 一行压缩：标题 | 公司 | 薪资 | 经验学历
        meta = " · ".join(
            str(item) for item in (card.get("experience"), card.get("degree")) if item
        )
        line = f"{index}. {card['title']} | {card['company']} | {card['salary']}"
        if meta:
            line += f" | {meta}"
        lines.append(line)

        if card.get("skills"):
            lines.append(f"   技能：{'、'.join(card['skills'][:5])}")
        if card.get("job_link"):
            lines.append(f"   链接：{card['job_link']}")

    lines.append("")
    lines.append(
        "界面已用卡片展示完整信息，你只需概述与回答提问，不要逐条复述。"
        "引用链接时必须用上面的「链接」，不得自行编造。"
    )
    if total > len(cards):
        lines.append(
            f"注意：库里共命中 {total} 条，这里只给了前 {len(cards)} 条。"
            "如果用户要更多，可以调整关键词或提高 limit 再查一次。"
        )
    return "\n".join(lines)


__all__ = ["JD_PREVIEW_CHARS", "build_job_cards", "build_job_model_text"]
