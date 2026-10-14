"""测试：P4 匹配度诊断（analyze_match）与报告解析。

这个工具的独特之处：它会**自己再调一次模型**产出结构化报告。
所以测试用假适配器注入（``ToolContext.adapter``），既能验证流程，
又不需要真实 API。

重点覆盖：
* 报告解析的容错（模型经常不守 JSON 规矩）
* 简历来源的优先级（本轮附件优先于历史附件）
* 缺 JD / 缺简历时的**明确报错**（不能让模型自己编）
* 规范化后前端渲染不会崩（字段补齐、类型修正、分数夹取）
"""

from __future__ import annotations

import json

import pytest

from bzp_workbench.models import ChatAttachment, ChatConversation, ChatMessage, JobRecord
from bzp_workbench.services.ai.tools import (
    AnalyzeMatchTool,
    ToolContext,
    ToolError,
    get_tool,
    tool_names,
    tools_for_mode,
)
from bzp_workbench.services.ai.tools.match import (
    DIMENSIONS,
    parse_report,
)
from bzp_workbench.services.llm import LLMResponse, TokenUsage


# ============================================================
# 假适配器
# ============================================================
class FakeAdapter:
    """返回预设文本的假适配器。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[list] = []
        self.kwargs: list[dict] = []

    async def chat_completion(self, messages, **kwargs):
        self.calls.append(list(messages))
        self.kwargs.append(kwargs)
        return LLMResponse(
            text=self.text, usage=TokenUsage(100, 200), model="fake"
        )


def sample_report(**overrides) -> dict:
    report = {
        "verdict": "技能基本匹配，但缺少大规模系统经验",
        "overall_score": 68,
        "dimensions": [
            {
                "name": "技能匹配",
                "level": "match",
                "summary": "核心技能对上",
                "evidence": ["简历写了 Python/FastAPI"],
                "gaps": [],
            },
            {
                "name": "经验匹配",
                "level": "partial",
                "summary": "年限够但项目体量偏小",
                "evidence": [],
                "gaps": ["没有高并发经验"],
            },
            {
                "name": "行业匹配",
                "level": "match",
                "summary": "同为互联网行业",
                "evidence": [],
                "gaps": [],
            },
            {
                "name": "风险点",
                "level": "partial",
                "summary": "两年换过三家公司",
                "evidence": [],
                "gaps": ["稳定性存疑"],
            },
        ],
        "strengths": ["有从 0 到 1 的完整项目经历"],
        "risks": [
            {"risk": "跳槽频繁", "why": "两年三家", "how_to_handle": "强调每次都是主动成长"}
        ],
        "questions": [
            {
                "question": f"第 {i} 题：讲讲你的项目",
                "category": "项目",
                "focus": "考察深度",
                "star": {
                    "situation": "背景",
                    "task": "任务",
                    "action": "行动",
                    "result": "结果",
                },
                "tips": "重点讲订单系统那段",
            }
            for i in range(1, 7)
        ],
        "actions": ["补齐高并发相关表述"],
    }
    report.update(overrides)
    return report


# ============================================================
# 数据准备
# ============================================================
@pytest.fixture(autouse=True)
async def _clean_p4_data():
    from sqlalchemy import delete

    from bzp_workbench.db import SessionLocal, init_db

    await init_db()
    async with SessionLocal() as session:
        await session.execute(delete(JobRecord).where(JobRecord.job_id.like("pytest-p4-%")))
        for model in (ChatAttachment, ChatMessage, ChatConversation):
            await session.execute(delete(model))
        await session.commit()
    yield
    async with SessionLocal() as session:
        await session.execute(delete(JobRecord).where(JobRecord.job_id.like("pytest-p4-%")))
        for model in (ChatAttachment, ChatMessage, ChatConversation):
            await session.execute(delete(model))
        await session.commit()


async def make_job() -> int:
    from bzp_workbench.db import SessionLocal

    async with SessionLocal() as session:
        job = JobRecord(
            job_id="pytest-p4-job",
            title="高级 Python 工程师",
            company="示例科技",
            salary="30-50K",
            city="深圳",
            job_duty="负责高并发交易系统",
            job_requirement="五年以上 Python，有大规模系统经验",
            job_link="https://www.zhipin.com/job_detail/pytest-p4-job.html",
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


async def make_conversation(with_resume: bool = True) -> int:
    from bzp_workbench.db import SessionLocal

    async with SessionLocal() as session:
        conversation = ChatConversation(title="pytest-p4", mode="interview_coach")
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)

        if with_resume:
            attachment = ChatAttachment(
                conversation_id=conversation.id,
                kind="pdf",
                filename="我的简历.pdf",
                mime_type="application/pdf",
                size_bytes=1234,
                stored_path="x.pdf",
                extract_status="done",
                extracted_text="张三，5 年 Python 经验，做过订单系统。",
            )
            session.add(attachment)
            await session.commit()
            await session.refresh(attachment)

            message = ChatMessage(
                conversation_id=conversation.id,
                role="user",
                content="帮我分析",
                attachments_json=json.dumps([attachment.id]),
            )
            session.add(message)
            await session.commit()

        return conversation.id


def make_context(db, conversation_id: int, adapter=None) -> ToolContext:
    return ToolContext(
        db=db,
        conversation_id=conversation_id,
        model_config_id=1,
        adapter=adapter,
    )


# ============================================================
# 注册与可见性
# ============================================================
class TestMatchToolRegistry:
    def test_registered(self):
        assert "analyze_match" in tool_names()
        assert get_tool("analyze_match") is not None

    def test_visible_in_both_relevant_modes(self):
        """面试辅导与找岗位都要能用。

        「这个岗位适合我吗」很容易被路由成 job_hunting（因为提到了"岗位"）。
        如果只在 interview_coach 暴露，模型在那个模式里就只能凭摘要硬编分析。
        """
        coach = {t.name for t in tools_for_mode("interview_coach")}
        hunting = {t.name for t in tools_for_mode("job_hunting")}
        assert "analyze_match" in coach
        assert "analyze_match" in hunting

    def test_not_visible_in_plain_chat(self):
        chat = {t.name for t in tools_for_mode("chat")}
        assert "analyze_match" not in chat, "闲聊模式不该暴露它（会白烧一次模型调用）"

    def test_definition_shape(self):
        definition = AnalyzeMatchTool().to_definition()
        assert definition.name == "analyze_match"
        props = definition.parameters["properties"]
        for key in ("local_id", "job_id", "jd_text", "resume_text", "focus"):
            assert key in props


# ============================================================
# 输入校验：该拦的必须拦住
# ============================================================
class TestAnalyzeMatchValidation:
    async def test_without_adapter_raises(self):
        from bzp_workbench.db import SessionLocal

        conversation_id = await make_conversation()
        async with SessionLocal() as db:
            with pytest.raises(ToolError) as excinfo:
                await AnalyzeMatchTool().run(
                    {"jd_text": "要求 Python"}, make_context(db, conversation_id, None)
                )
        assert "适配器" in excinfo.value.message

    async def test_without_jd_raises_and_forbids_inventing(self):
        """没有目标 JD 时必须拦住并禁止模型自己编一份。"""
        from bzp_workbench.db import SessionLocal

        conversation_id = await make_conversation()
        async with SessionLocal() as db:
            with pytest.raises(ToolError) as excinfo:
                await AnalyzeMatchTool().run(
                    {}, make_context(db, conversation_id, FakeAdapter("{}"))
                )
        hint = excinfo.value.hint or ""
        assert "不要自己编" in hint or "确认" in hint

    async def test_without_resume_raises_with_actionable_hint(self):
        """没有简历时要说清怎么办，特别是"图片简历不能用"。"""
        from bzp_workbench.db import SessionLocal

        job_id = await make_job()
        conversation_id = await make_conversation(with_resume=False)
        async with SessionLocal() as db:
            with pytest.raises(ToolError) as excinfo:
                await AnalyzeMatchTool().run(
                    {"local_id": job_id},
                    make_context(db, conversation_id, FakeAdapter("{}")),
                )
        hint = excinfo.value.hint or ""
        assert "上传" in hint
        assert "图片" in hint, "必须说明图片简历无法用于文本抽取"

    async def test_unknown_job_id_raises(self):
        from bzp_workbench.db import SessionLocal

        conversation_id = await make_conversation()
        async with SessionLocal() as db:
            with pytest.raises(ToolError) as excinfo:
                await AnalyzeMatchTool().run(
                    {"local_id": 99999999},
                    make_context(db, conversation_id, FakeAdapter("{}")),
                )
        assert "JD" in excinfo.value.message


# ============================================================
# 正常流程
# ============================================================
class TestAnalyzeMatchExecution:
    async def test_generates_report_from_attachment(self):
        from bzp_workbench.db import SessionLocal

        job_id = await make_job()
        conversation_id = await make_conversation()
        adapter = FakeAdapter(json.dumps(sample_report(), ensure_ascii=False))

        async with SessionLocal() as db:
            result = await AnalyzeMatchTool().run(
                {"local_id": job_id, "focus": "重点看技术深度"},
                make_context(db, conversation_id, adapter),
            )

        assert result.payload_type == "match_report"
        report = result.data["report"]
        assert report["overall_score"] == 68
        assert len(report["dimensions"]) == 4
        assert len(report["questions"]) == 6
        assert result.data["job"]["title"] == "高级 Python 工程师"
        assert "我的简历.pdf" in result.data["resume_source"]

        # 必须把简历内容真的送进了模型请求
        sent = " ".join(m.content or "" for m in adapter.calls[0])
        assert "5 年 Python 经验" in sent
        assert "高并发交易系统" in sent
        assert "重点看技术深度" in sent

    async def test_prompt_uses_low_temperature(self):
        """诊断报告要的是稳定判断，不是创造性发挥。"""
        from bzp_workbench.db import SessionLocal

        job_id = await make_job()
        conversation_id = await make_conversation()
        adapter = FakeAdapter(json.dumps(sample_report(), ensure_ascii=False))
        async with SessionLocal() as db:
            await AnalyzeMatchTool().run(
                {"local_id": job_id}, make_context(db, conversation_id, adapter)
            )
        assert adapter.kwargs[0].get("temperature") == 0.2

    async def test_accepts_pasted_jd(self):
        from bzp_workbench.db import SessionLocal

        conversation_id = await make_conversation()
        adapter = FakeAdapter(json.dumps(sample_report(), ensure_ascii=False))
        async with SessionLocal() as db:
            result = await AnalyzeMatchTool().run(
                {"jd_text": "岗位要求：精通 Rust，五年经验"},
                make_context(db, conversation_id, adapter),
            )
        assert result.data["job"] is None
        assert "粘贴" in result.data["jd_source"]
        sent = " ".join(m.content or "" for m in adapter.calls[0])
        assert "精通 Rust" in sent

    async def test_model_text_forbids_repeating_the_report(self):
        """给主模型的文本必须要求"只做导读"，否则会和卡片重复且很长。"""
        from bzp_workbench.db import SessionLocal

        job_id = await make_job()
        conversation_id = await make_conversation()
        adapter = FakeAdapter(json.dumps(sample_report(), ensure_ascii=False))
        async with SessionLocal() as db:
            result = await AnalyzeMatchTool().run(
                {"local_id": job_id}, make_context(db, conversation_id, adapter)
            )
        assert "不要逐条复述" in result.model_text
        assert "卡片" in result.model_text

    async def test_bad_json_falls_back_instead_of_failing(self):
        """模型没输出合法 JSON 时降级为纯文本，而不是让整个工具失败。

        用户至少能看到内容；界面会提示格式异常。
        """
        from bzp_workbench.db import SessionLocal

        job_id = await make_job()
        conversation_id = await make_conversation()
        adapter = FakeAdapter("我觉得这个人挺合适的，建议面试。")

        async with SessionLocal() as db:
            result = await AnalyzeMatchTool().run(
                {"local_id": job_id}, make_context(db, conversation_id, adapter)
            )

        assert result.payload_type == "match_report"
        report = result.data["report"]
        assert report["parse_error"] is True
        assert "建议面试" in report["raw_text"]
        # 必须提醒模型格式异常，让它用文字讲清楚
        assert "格式异常" in result.model_text

    async def test_llm_failure_becomes_tool_error(self):
        """模型调用失败要转成 ToolError（让主模型知道并换策略）。"""
        from bzp_workbench.db import SessionLocal
        from bzp_workbench.services.llm import LLMError, LLMErrorType

        class BrokenAdapter:
            async def chat_completion(self, *args, **kwargs):
                raise LLMError(LLMErrorType.RATE_LIMITED, "触发限流")

        job_id = await make_job()
        conversation_id = await make_conversation()
        async with SessionLocal() as db:
            with pytest.raises(ToolError) as excinfo:
                await AnalyzeMatchTool().run(
                    {"local_id": job_id},
                    make_context(db, conversation_id, BrokenAdapter()),
                )
        assert "限流" in excinfo.value.message

    async def test_payload_is_json_serializable(self):
        from bzp_workbench.db import SessionLocal

        job_id = await make_job()
        conversation_id = await make_conversation()
        adapter = FakeAdapter(json.dumps(sample_report(), ensure_ascii=False))
        async with SessionLocal() as db:
            result = await AnalyzeMatchTool().run(
                {"local_id": job_id}, make_context(db, conversation_id, adapter)
            )
        json.dumps(result.data)  # 要存进 content_json


class TestResumeSourcePriority:
    async def test_current_attachment_wins_over_history(self):
        """用户换了一份简历再问时，必须用新的那份。

        用旧的会给出完全错误的诊断 —— 这比"没找到简历"更糟，
        因为用户不会察觉。
        """
        from sqlalchemy import select

        from bzp_workbench.db import SessionLocal

        job_id = await make_job()
        conversation_id = await make_conversation()  # 已有一份历史简历

        adapter = FakeAdapter(json.dumps(sample_report(), ensure_ascii=False))

        async with SessionLocal() as db:
            # 再追加一份更新的简历，并让它成为本轮消息的附件
            newer = ChatAttachment(
                conversation_id=conversation_id,
                kind="docx",
                filename="新简历.docx",
                size_bytes=999,
                stored_path="new.docx",
                extract_status="done",
                extracted_text="李四，8 年 Rust 经验，主导过分布式存储。",
            )
            db.add(newer)
            await db.commit()
            await db.refresh(newer)

            latest = ChatMessage(
                conversation_id=conversation_id,
                role="user",
                content="这是我的新简历",
                attachments_json=json.dumps([newer.id]),
            )
            db.add(latest)
            await db.commit()

            result = await AnalyzeMatchTool().run(
                {"local_id": job_id}, make_context(db, conversation_id, adapter)
            )

        assert "新简历.docx" in result.data["resume_source"]
        sent = " ".join(m.content or "" for m in adapter.calls[0])
        assert "8 年 Rust 经验" in sent, "必须用最新的简历"
        assert "5 年 Python 经验" not in sent, "不该把旧简历也塞进去"

    async def test_explicit_resume_text_wins(self):
        from bzp_workbench.db import SessionLocal

        job_id = await make_job()
        conversation_id = await make_conversation()
        adapter = FakeAdapter(json.dumps(sample_report(), ensure_ascii=False))
        async with SessionLocal() as db:
            result = await AnalyzeMatchTool().run(
                {
                    "local_id": job_id,
                    "resume_text": "王五，10 年 Go 经验",
                },
                make_context(db, conversation_id, adapter),
            )
        assert "调用方传入" in result.data["resume_source"]
        sent = " ".join(m.content or "" for m in adapter.calls[0])
        assert "王五" in sent


# ============================================================
# 报告解析与规范化
# ============================================================
class TestReportParsing:
    def test_standard(self):
        report = parse_report(json.dumps(sample_report(), ensure_ascii=False))
        assert report is not None
        assert report["overall_score"] == 68
        assert [d["name"] for d in report["dimensions"]] == list(DIMENSIONS)

    def test_markdown_fence(self):
        raw = "```json\n" + json.dumps(sample_report(), ensure_ascii=False) + "\n```"
        assert parse_report(raw)["overall_score"] == 68

    def test_prose_wrapped(self):
        raw = (
            "好的，报告如下：\n"
            + json.dumps({"verdict": "还行", "overall_score": 55}, ensure_ascii=False)
            + "\n以上。"
        )
        assert parse_report(raw)["overall_score"] == 55

    def test_score_clamped(self):
        assert parse_report('{"verdict": "x", "overall_score": 150}')["overall_score"] == 100
        assert parse_report('{"verdict": "x", "overall_score": -20}')["overall_score"] == 0

    def test_string_score_accepted(self):
        assert parse_report('{"verdict": "x", "overall_score": "80"}')["overall_score"] == 80

    def test_unparseable_returns_none(self):
        assert parse_report("完全不是 JSON") is None
        assert parse_report("") is None
        assert parse_report(None) is None

    def test_missing_dimensions_are_filled(self):
        """缺维度也要补齐四行，否则前端渲染会缺块或崩。"""
        report = parse_report('{"verdict": "总体一般", "overall_score": 50}')
        assert report is not None
        assert len(report["dimensions"]) == 4
        assert [d["name"] for d in report["dimensions"]] == list(DIMENSIONS)
        for dim in report["dimensions"]:
            assert dim["level"] in ("match", "partial", "mismatch")
            assert dim["summary"]

    def test_chinese_level_normalized(self):
        raw = json.dumps(
            {
                "dimensions": [
                    {"name": "技能匹配", "level": "不匹配"},
                    {"name": "经验匹配", "level": "匹配"},
                    {"name": "行业匹配", "level": "部分匹配"},
                ]
            },
            ensure_ascii=False,
        )
        report = parse_report(raw)
        levels = {d["name"]: d["level"] for d in report["dimensions"]}
        assert levels["技能匹配"] == "mismatch"
        assert levels["经验匹配"] == "match"
        assert levels["行业匹配"] == "partial"
        assert levels["风险点"] == "partial"  # 缺失的那个

    def test_unknown_level_becomes_partial(self):
        raw = json.dumps(
            {"dimensions": [{"name": "技能匹配", "level": "八成行"}]}, ensure_ascii=False
        )
        assert parse_report(raw)["dimensions"][0]["level"] == "partial"

    def test_risks_and_questions_accept_plain_strings(self):
        """模型偶尔把对象数组写成字符串数组，不能因此丢掉内容。"""
        raw = json.dumps(
            {"risks": ["跳槽频繁"], "questions": ["介绍一下你自己"]}, ensure_ascii=False
        )
        report = parse_report(raw)
        assert report["risks"][0]["risk"] == "跳槽频繁"
        assert report["questions"][0]["question"] == "介绍一下你自己"
        # 缺 star 也要补全四段，否则前端访问 star.situation 会报错
        assert set(report["questions"][0]["star"]) == {
            "situation",
            "task",
            "action",
            "result",
        }

    def test_question_without_text_is_dropped(self):
        raw = json.dumps({"questions": [{"focus": "只有考察点"}]}, ensure_ascii=False)
        assert parse_report(raw)["questions"] == []

    def test_null_fields_do_not_crash(self):
        raw = json.dumps(
            {
                "verdict": "结论在这里",
                "overall_score": None,
                "dimensions": None,
                "strengths": None,
                "risks": None,
                "questions": None,
                "actions": None,
            },
            ensure_ascii=False,
        )
        report = parse_report(raw)
        assert report is not None
        assert report["verdict"] == "结论在这里"
        assert report["overall_score"] == 0
        assert len(report["dimensions"]) == 4
        assert report["questions"] == []


class TestTruncationRepair:
    """**回归测试**：输出被 max_tokens 截断时也要尽量救回内容。

    实测踩过的坑：一份报告要 4 个维度 + 若干风险点 + 5-8 道带完整 STAR 的
    面试题，很容易超过 6000 token。第一版把 max_tokens 设成 4096，
    结果 ``finish_reason=length``、JSON 被硬截断、整份报告解析失败 ——
    用户看到的是"格式异常，什么都没有"，而不是"报告不完整"。

    现在两道防线：① max_tokens 提到 8192；② 解析时按截断修复。
    """

    def test_truncated_mid_string_is_repaired(self):
        """在某个字符串中间被切断 —— 这是最常见的形态。"""
        raw = (
            '{"verdict": "技能栈错配", "overall_score": 30, '
            '"dimensions": [{"name": "技能匹配", "level": "mismatch", '
            '"summary": "JD 要求 Java，简历全是 Python，完全'
        )
        report = parse_report(raw)
        assert report is not None, "应当能救回部分报告"
        assert report["overall_score"] == 30
        assert report["verdict"] == "技能栈错配"
        # 四个维度都要补齐（缺的用占位）
        assert len(report["dimensions"]) == 4
        assert report["dimensions"][0]["level"] == "mismatch"

    def test_truncated_drops_incomplete_question(self):
        """最后一道题只写了一半 —— 应该丢掉它，保留前面完整的题。"""
        raw = (
            '{"overall_score": 50, "questions": ['
            '{"question": "第一题完整", "star": {"situation": "s", "task": "t", '
            '"action": "a", "result": "r"}},'
            '{"question": "第二题完整", "star": {"situation": "s", "task": "t", '
            '"action": "a", "result": "r"}},'
            '{"question": "第三题只写了一半'
        )
        report = parse_report(raw)
        assert report is not None
        questions = report["questions"]
        assert len(questions) >= 1, "至少应保住完整的题"
        assert all(q["question"] for q in questions)
        assert "第三题只写了一半" not in [q["question"] for q in questions]

    def test_truncated_inside_array(self):
        raw = '{"overall_score": 40, "strengths": ["亮点一", "亮点二", "亮点三'
        report = parse_report(raw)
        assert report is not None
        assert report["overall_score"] == 40
        assert "亮点一" in report["strengths"]

    def test_complete_json_still_parses_normally(self):
        """修复逻辑不能影响正常输入。"""
        report = parse_report(json.dumps(sample_report(), ensure_ascii=False))
        assert report["overall_score"] == 68
        assert len(report["questions"]) == 6

    def test_repair_does_not_invent_data(self):
        """修复只能丢尾部，不能凭空造字段值。"""
        raw = '{"overall_score": 33, "dimensions": [{"name": "技能匹配", "level": "partial", "summary": "断在这里'
        report = parse_report(raw)
        assert report["overall_score"] == 33
        assert report["questions"] == []
        assert report["risks"] == []
        assert "亮点" not in report["verdict"]

    def test_unrepairable_returns_none(self):
        """实在救不回来时返回 None（由调用方降级为纯文本）。

        特别是 ``"{"``：它技术上能被"修复"成空对象，但那样会渲染出一张
        全是占位值（0 分、四个"未覆盖"）的卡片 —— 比看到原始文本更糟，
        用户会以为模型真的给了 0 分。
        """
        assert parse_report('{"overall_score":') is None
        assert parse_report("{") is None
        assert parse_report("[[[") is None
        # 只有分数、没有任何结论内容，也算空壳
        assert parse_report('{"overall_score": 88}') is None

    def test_generate_max_tokens_is_large_enough(self):
        """守住这个常量：小于 6000 会让长报告被截断。"""
        from bzp_workbench.services.ai.tools.match import GENERATE_MAX_TOKENS

        assert GENERATE_MAX_TOKENS >= 6000, (
            "实测完整报告可达 6000+ token；上限太小会截断 JSON"
        )
