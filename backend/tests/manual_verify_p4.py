"""真机验证 P4：匹配度诊断报告 + STAR 面试题。

会**真的调用 DeepSeek**，并真的让主模型调用 analyze_match（内部再调一次模型）。
所以一次完整流程大约是 2-3 次模型调用。

前置条件：
* 后端在 127.0.0.1:18080 运行
* 授权档案为 active（analyze_match 本身不需要登录，但工具链会检查；
  如果没有 active 档案本脚本会临时设置，结束后恢复）
* 库里至少有一条带 JD 的岗位（脚本会自动挑一条）

用法（在 backend/ 目录下）：
    python tests/manual_verify_p4.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from bzp_workbench.config import settings

settings.fake_llm = False

from sqlalchemy import select

from bzp_workbench.db import SessionLocal
from bzp_workbench.enums import AuthStatus
from bzp_workbench.models import (
    AuthProfile,
    ChatAttachment,
    ChatConversation,
    ChatMessage,
    JobRecord,
)
from bzp_workbench.services.ai import service as chat_service

#: 一份刻意"有明显短板"的简历，用来验证诊断是否会指出缺口
RESUME_TEXT = """张三
求职意向：Python 后端开发
工作年限：3 年

【教育经历】
2016-2020  某大学  计算机科学与技术  本科

【工作经历】
2020.07 - 2022.03  某小型外包公司  初级后端开发
  - 使用 Django 开发企业内部管理系统
  - 负责简单的 CRUD 接口与后台管理页面
  - 团队 3 人，日活用户约 200

2022.04 - 至今  某创业公司  后端开发
  - 使用 FastAPI 重构订单模块
  - 引入 Redis 做缓存，QPS 从 300 提升到 800
  - 参与部分数据同步任务，用 Celery 做异步

【技能】
Python、Django、FastAPI、MySQL、Redis、Celery、Git
了解 Docker，没有实际生产环境容器化经验

【项目】
订单系统重构：负责接口层与缓存设计，日订单量约 5000
"""


#: 关键词：用来挑一个靠谱的后端岗位（避免随机挑到"跨境电商客服"这类
#: 与简历完全不搭的岗位，那样测的是"模型会不会拒绝"，不是"报告能不能生成"）
TARGET_KEYWORDS = ("Python", "后端", "AI Agent", "Django", "FastAPI")


async def pick_job():
    """挑一条 JD 完整、且与简历方向相关的岗位。"""
    async with SessionLocal() as db:
        for keyword in TARGET_KEYWORDS:
            job = (
                await db.execute(
                    select(JobRecord)
                    .where(
                        JobRecord.jd.is_not(None),
                        JobRecord.jd != "",
                        JobRecord.title.like(f"%{keyword}%"),
                    )
                    .order_by(JobRecord.id.desc())
                    .limit(1)
                )
            ).scalars().first()
            if job is not None:
                return job
        # 退而求其次：任意一条有 JD 的
        return (
            await db.execute(
                select(JobRecord)
                .where(JobRecord.jd.is_not(None), JobRecord.jd != "")
                .order_by(JobRecord.id.desc())
                .limit(1)
            )
        ).scalars().first()


async def main() -> int:
    job = await pick_job()
    if job is None:
        print("库里没有带 JD 的岗位，无法验证。请先抓一次带详情的任务。")
        return 1
    job_id, job_title, job_company = job.id, job.title, job.company
    jd_preview = (job.jd or "")[:150].replace("\n", " ")

    print(f"目标岗位：#{job_id} {job_title} @ {job_company}")
    print(f"JD 预览：{jd_preview}…\n")

    # ---- 准备会话与简历附件 ----
    profile_backup: str | None = None
    async with SessionLocal() as db:
        profile = await db.get(AuthProfile, 1)
        if profile is not None and profile.status != AuthStatus.ACTIVE.value:
            profile_backup = profile.status
            profile.status = AuthStatus.ACTIVE.value
            await db.commit()
            print(f"（临时把授权状态从 {profile_backup} 设为 active，结束时恢复）")

        conversation = ChatConversation(title="P4 真机验证", mode="interview_coach")
        db.add(conversation)
        await db.commit()
        await db.refresh(conversation)
        conversation_id = conversation.id

        attachment = ChatAttachment(
            conversation_id=conversation_id,
            kind="text",
            filename="张三的简历.txt",
            mime_type="text/plain",
            size_bytes=len(RESUME_TEXT.encode("utf-8")),
            stored_path="(内置测试简历)",
            extract_status="done",
            extracted_text=RESUME_TEXT,
        )
        db.add(attachment)
        await db.commit()
        await db.refresh(attachment)
        attachment_id = attachment.id

    user_text = f"帮我分析一下我的简历和这个岗位（岗位 #{job_id}）的匹配度，并预测面试题"

    print(f"会话 #{conversation_id}，简历附件 #{attachment_id}")
    print(f"用户：{user_text}\n")
    print("=" * 76)

    try:
        async with SessionLocal() as db:
            conversation = await db.get(ChatConversation, conversation_id)
            await chat_service.persist_user_message(
                db,
                conversation,
                text=user_text,
                attachment_ids=[attachment_id],
            )
            await db.refresh(conversation)

            meta: dict = {}
            counts: dict[str, int] = {}
            texts: list[str] = []
            tool_calls: list[dict] = []
            tool_results: list[dict] = []
            errors: list[dict] = []
            usage: dict = {}

            async for frame in chat_service.stream_reply(
                db,
                conversation,
                user_text=user_text,
                attachment_ids=[attachment_id],
                forced_mode="interview_coach",
            ):
                name = frame.event.value
                counts[name] = counts.get(name, 0) + 1
                if name == "meta":
                    meta = frame.data
                elif name == "text":
                    texts.append(frame.data.get("delta", ""))
                elif name == "tool_call":
                    tool_calls.append(frame.data)
                elif name == "tool_result":
                    tool_results.append(frame.data)
                elif name == "error":
                    errors.append(frame.data)
                elif name == "usage":
                    usage = frame.data

        print(f"模式：{meta.get('mode')}（{meta.get('mode_reason')}）")
        print(f"事件：{counts}")
        print(
            f"Token：prompt={usage.get('prompt_tokens')} "
            f"completion={usage.get('completion_tokens')} "
            f"工具轮数={usage.get('tool_rounds')}"
        )

        for call in tool_calls:
            print(f"\n工具调用：{call.get('name')}  参数={call.get('arguments')}")

        report = None
        for result in tool_results:
            data = result.get("data") or {}
            print(f"工具结果：{result.get('name')} -> {result.get('payload_type')}")
            if result.get("payload_type") == "match_report":
                report = data.get("report") or {}
                print(f"  简历来源：{data.get('resume_source')}")
                print(f"  岗位来源：{data.get('jd_source')}")

        if report:
            print("\n" + "=" * 76)
            print("《匹配度诊断报告》")
            print("=" * 76)
            print(f"综合匹配度：{report.get('overall_score')}/100")
            print(f"结论：{report.get('verdict')}")
            if report.get("parse_error"):
                print("⚠️ 报告解析失败（这是需要关注的问题）")

            print("\n四维诊断：")
            for dim in report.get("dimensions") or []:
                print(f"  [{dim.get('level'):8}] {dim.get('name')}：{dim.get('summary')}")
                for gap in dim.get("gaps") or []:
                    print(f"             缺口：{gap}")

            print(f"\n亮点（{len(report.get('strengths') or [])} 条）：")
            for item in (report.get("strengths") or [])[:3]:
                print(f"  · {item}")

            print(f"\n风险点（{len(report.get('risks') or [])} 个）：")
            for risk in (report.get("risks") or [])[:3]:
                print(f"  · {risk.get('risk')}")
                if risk.get("how_to_handle"):
                    print(f"      应对：{risk['how_to_handle']}")

            questions = report.get("questions") or []
            print(f"\nSTAR 面试题（{len(questions)} 道）：")
            for index, item in enumerate(questions, start=1):
                print(f"\n  {index}. [{item.get('category')}] {item.get('question')}")
                if item.get("focus"):
                    print(f"     考察点：{item['focus']}")
                star = item.get("star") or {}
                print(f"     S：{(star.get('situation') or '')[:70]}")
                print(f"     T：{(star.get('task') or '')[:70]}")
                print(f"     A：{(star.get('action') or '')[:70]}")
                print(f"     R：{(star.get('result') or '')[:70]}")
                if item.get("tips"):
                    print(f"     提示：{item['tips'][:90]}")

            print(f"\n后续建议（{len(report.get('actions') or [])} 条）：")
            for item in report.get("actions") or []:
                print(f"  · {item}")

        if errors:
            print("\n错误：")
            for error in errors:
                print(f"  {error.get('title')} — {error.get('message')}")
                print(f"    建议：{error.get('suggestion')}")

        answer = "".join(texts)
        print(f"\n{'=' * 76}")
        print(f"主模型回答（{len(answer)} 字）：")
        for line in answer.strip().splitlines()[:20]:
            print(f"  {line}")

        # ---- 结论 ----
        print(f"\n{'=' * 76}")
        ok = bool(report) and not report.get("parse_error")
        questions_count = len((report or {}).get("questions") or [])
        print(f"报告生成：{'成功' if report else '失败'}")
        print(f"结构化解析：{'成功' if ok else '失败'}")
        print(f"面试题数量：{questions_count}（期望 5-8）")
        in_range = 5 <= questions_count <= 8
        print(f"题目数量是否达标：{'是' if in_range else '否'}")
        return 0 if (ok and in_range) else 1
    finally:
        # ---- 清理与恢复 ----
        # 恢复授权状态（本脚本为验证流程临时设过 active）
        if profile_backup is not None:
            async with SessionLocal() as db:
                profile = await db.get(AuthProfile, 1)
                if profile is not None:
                    profile.status = profile_backup
                    await db.commit()

        # 走共享清理工具：它按"标题是否能在自己的消息里找到出处"判断残留，
        # 避免误伤用户的真实对话
        from manual_cleanup import cleanup_verification_data

        summary = await cleanup_verification_data(
            extra_conversation_ids=[conversation_id]
        )
        print(
            f"\n（测试数据已清理：任务 {summary['tasks']} 条、"
            f"对话 {summary['conversations']} 个；授权状态已恢复）"
        )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
