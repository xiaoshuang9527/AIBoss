"""测试：P6（上下文预算裁剪 / 用量统计 / 对话搜索）。

三块各自的重点：

* **裁剪**：必须真的挡住 token 增长，同时不丢 system prompt 与最近几轮
* **用量统计**：口径要对（缓存命中率、思考占比），且**绝不能出现计费字眼**
* **搜索**：既要能按标题命中，也要能按**消息内容**命中（用户常记得
  "我问过 Docker 那个对话"，而标题未必包含那个词）
"""

from __future__ import annotations

import pytest

from bzp_workbench.services.ai.tokens import (
    CHARS_PER_TOKEN,
    MIN_RECENT_MESSAGES,
    PROMPT_TOKEN_BUDGET,
    compress_tool_result,
    estimate_tokens,
    trim_messages,
)
from bzp_workbench.services.llm import Message, Role


# ============================================================
# Token 估算
# ============================================================
class TestTokenEstimation:
    def test_empty(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens(None) == 0

    def test_scales_linearly(self):
        short = estimate_tokens("a" * 100)
        long = estimate_tokens("a" * 1000)
        assert long > short
        assert long == pytest.approx(1000 / CHARS_PER_TOKEN, abs=2)

    def test_never_zero_for_nonempty(self):
        # 极短文本也要算 1，否则一堆短消息会被算成 0
        assert estimate_tokens("x") >= 1

    def test_chinese_counted(self):
        assert estimate_tokens("这是一段中文测试内容") >= 5


# ============================================================
# 上下文裁剪
# ============================================================
def build_history(count: int, chars: int = 400) -> list[Message]:
    """造一段对话历史（user/assistant 交替）。"""
    messages: list[Message] = []
    for index in range(count):
        role = Message.user if index % 2 == 0 else Message.assistant
        messages.append(role(f"消息{index}：" + "内容" * (chars // 2)))
    return messages


class TestTrimMessages:
    def test_short_history_untouched(self):
        system = [Message.system("系统提示")]
        history = build_history(4, chars=50)
        report = trim_messages(history, system_messages=system)
        assert report.dropped_count == 0
        # system + 全部历史
        assert len(report.messages) == len(history) + 1

    def test_system_prompt_never_dropped(self):
        """丢了 system prompt 模型就失忆了 —— 这是最重要的一条。"""
        system = [Message.system("你是求职助手" + "很长" * 2000)]
        history = build_history(40, chars=2000)
        report = trim_messages(history, system_messages=system)
        assert report.messages[0].role is Role.SYSTEM
        assert report.messages[0].content.startswith("你是求职助手")

    def test_keeps_recent_messages(self):
        system = [Message.system("s")]
        history = build_history(60, chars=3000)
        report = trim_messages(history, system_messages=system)
        # 最近 6 条必须还在（否则对话不连贯）
        tail = report.messages[-(MIN_RECENT_MESSAGES):]
        assert [m.content for m in tail] == [m.content for m in history[-MIN_RECENT_MESSAGES:]]

    def test_long_history_is_trimmed(self):
        system = [Message.system("s")]
        history = build_history(80, chars=2000)
        report = trim_messages(history, system_messages=system)
        assert report.dropped_count > 0, "超长历史必须被裁剪"
        assert report.estimated_tokens <= PROMPT_TOKEN_BUDGET * 1.3

    def test_oldest_dropped_first(self):
        """丢的必须是最旧的，不能丢中间的（否则对话逻辑会断裂）。"""
        system = [Message.system("s")]
        history = build_history(80, chars=2000)
        report = trim_messages(history, system_messages=system)
        kept_contents = {m.content for m in report.messages[1:]}
        # 最后一条一定还在
        assert history[-1].content in kept_contents
        # 第一条一定被丢了
        assert history[0].content not in kept_contents

    def test_small_budget_still_keeps_minimum(self):
        """预算极小也不能把历史清空 —— 宁可超预算也要保住连贯性。"""
        system = [Message.system("s")]
        history = build_history(30, chars=3000)
        report = trim_messages(history, system_messages=system, budget=100)
        # system + 至少 MIN_RECENT_MESSAGES 条
        assert len(report.messages) >= MIN_RECENT_MESSAGES

    def test_tool_results_compressed(self):
        """历史里的工具结果要被压缩 —— 否则一次检索就撑爆后续每轮。"""
        system = [Message.system("s")]
        tool_message = Message.tool_result("call-1", "岗位数据" * 2000)
        history = [Message.user("找岗位"), Message.assistant("好的"), tool_message]
        report = trim_messages(history, system_messages=system)
        assert report.compressed_count == 1
        compressed = report.messages[-1]
        assert len(compressed.content or "") < len("岗位数据" * 2000)
        assert "已压缩" in (compressed.content or "")
        # 压缩后仍要保留开头，让模型知道当时查到了什么
        assert "岗位数据" in (compressed.content or "")

    def test_no_compression_when_short(self):
        system = [Message.system("s")]
        history = [Message.tool_result("call-1", "短结果")]
        report = trim_messages(history, system_messages=system)
        assert report.compressed_count == 0
        assert report.messages[-1].content == "短结果"

    def test_report_flags(self):
        system = [Message.system("s")]
        history = build_history(80, chars=2000)
        report = trim_messages(history, system_messages=system)
        assert report.trimmed is True
        assert report.budget == PROMPT_TOKEN_BUDGET

    def test_estimate_matches_output(self):
        """report.estimated_tokens 要反映真实保留量（不能虚报）。"""
        system = [Message.system("系统")]
        history = build_history(50, chars=1500)
        report = trim_messages(history, system_messages=system)
        actual = sum(estimate_tokens(m.content) for m in report.messages)
        assert report.estimated_tokens == actual


class TestCompressToolResult:
    def test_short_untouched(self):
        assert compress_tool_result("短") == "短"

    def test_long_truncated_with_notice(self):
        raw = "x" * 5000
        out = compress_tool_result(raw, limit=100)
        assert len(out) < 200
        assert out.startswith("x" * 100)
        assert "5000" in out, "必须告知原文多长，否则模型以为这就是全部"

    def test_none_safe(self):
        assert compress_tool_result(None) == ""


# ============================================================
# 用量统计
# ============================================================
@pytest.fixture(autouse=True)
async def _clean_usage_data():
    from sqlalchemy import delete

    from bzp_workbench.db import SessionLocal, init_db
    from bzp_workbench.models import ChatAttachment, ChatConversation, ChatMessage

    await init_db()
    async with SessionLocal() as session:
        await session.execute(delete(ChatAttachment))
        await session.execute(delete(ChatMessage))
        await session.execute(delete(ChatConversation))
        await session.commit()
    yield
    async with SessionLocal() as session:
        await session.execute(delete(ChatAttachment))
        await session.execute(delete(ChatMessage))
        await session.execute(delete(ChatConversation))
        await session.commit()


async def seed_conversation(
    *,
    title: str = "测试对话",
    mode: str = "job_hunting",
    user_text: str = "帮我找岗位",
    assistant_text: str = "好的",
    prompt: int = 100,
    completion: int = 50,
    cached: int = 40,
    reasoning: int = 20,
    model_label: str = "测试模型",
    latency: int = 1000,
) -> int:
    from bzp_workbench.db import SessionLocal
    from bzp_workbench.models import ChatConversation, ChatMessage

    async with SessionLocal() as session:
        conversation = ChatConversation(title=title, mode=mode)
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)

        session.add(
            ChatMessage(
                conversation_id=conversation.id,
                role="user",
                content=user_text,
            )
        )
        session.add(
            ChatMessage(
                conversation_id=conversation.id,
                role="assistant",
                content=assistant_text,
                prompt_tokens=prompt,
                completion_tokens=completion,
                cached_tokens=cached,
                reasoning_tokens=reasoning,
                model_label=model_label,
                latency_ms=latency,
            )
        )
        await session.commit()
        return conversation.id


class TestUsageReport:
    async def test_empty_database(self):
        from bzp_workbench.db import SessionLocal
        from bzp_workbench.services.ai.analytics import usage_report

        async with SessionLocal() as db:
            report = await usage_report(db)
        assert report["totals"]["total_tokens"] == 0
        assert report["totals"]["cache_hit_rate"] == 0.0
        assert report["by_model"] == []
        assert report["tools"] == []

    async def test_totals_and_rates(self):
        from bzp_workbench.db import SessionLocal
        from bzp_workbench.services.ai.analytics import usage_report

        await seed_conversation(prompt=1000, completion=500, cached=400, reasoning=300)
        async with SessionLocal() as db:
            report = await usage_report(db)
        totals = report["totals"]
        assert totals["total_tokens"] == 1500
        assert totals["prompt_tokens"] == 1000
        assert totals["completion_tokens"] == 500
        # 缓存命中率 = 命中 / 输入
        assert totals["cache_hit_rate"] == pytest.approx(0.4)
        # 思考占比 = 思考 / 输出
        assert totals["reasoning_share"] == pytest.approx(0.6)

    async def test_only_assistant_messages_counted(self):
        """用户消息没有 token 字段，不该把它的 0 也算进去影响均值。"""
        from bzp_workbench.db import SessionLocal
        from bzp_workbench.services.ai.analytics import usage_report

        await seed_conversation()
        async with SessionLocal() as db:
            report = await usage_report(db)
        assert report["totals"]["assistant_messages"] == 1

    async def test_by_model_groups(self):
        from bzp_workbench.db import SessionLocal
        from bzp_workbench.services.ai.analytics import usage_report

        await seed_conversation(title="A", model_label="快模型", prompt=100, latency=800)
        await seed_conversation(title="B", model_label="慢模型", prompt=200, latency=12000)
        await seed_conversation(title="C", model_label="慢模型", prompt=300, latency=12000)

        async with SessionLocal() as db:
            report = await usage_report(db)
        by_model = {item["model_label"]: item for item in report["by_model"]}
        assert set(by_model) == {"快模型", "慢模型"}
        # 按用量降序
        assert report["by_model"][0]["model_label"] == "慢模型"
        assert by_model["慢模型"]["messages"] == 2
        # prompt 200 + 300，completion 默认 50 × 2
        assert by_model["慢模型"]["total_tokens"] == 600
        assert by_model["慢模型"]["avg_latency_ms"] == 12000

    async def test_top_conversations_sorted(self):
        from bzp_workbench.db import SessionLocal
        from bzp_workbench.services.ai.analytics import usage_report

        await seed_conversation(title="小", prompt=10)
        await seed_conversation(title="大", prompt=9999)
        async with SessionLocal() as db:
            report = await usage_report(db)
        assert report["top_conversations"][0]["title"] == "大"

    async def test_tools_counted_from_json(self):
        from bzp_workbench.db import SessionLocal
        from bzp_workbench.models import ChatConversation, ChatMessage
        from bzp_workbench.services.ai.analytics import usage_report

        async with SessionLocal() as db:
            conversation = ChatConversation(title="带工具", mode="job_hunting")
            db.add(conversation)
            await db.commit()
            await db.refresh(conversation)
            db.add(
                ChatMessage(
                    conversation_id=conversation.id,
                    role="assistant",
                    content="查到了",
                    tool_calls_json=(
                        '[{"id":"1","name":"search_existing_jobs","arguments":{}},'
                        '{"id":"2","name":"get_job_detail","arguments":{}}]'
                    ),
                    prompt_tokens=10,
                )
            )
            await db.commit()

        async with SessionLocal() as db:
            report = await usage_report(db)
        tools = {item["name"]: item["calls"] for item in report["tools"]}
        assert tools.get("search_existing_jobs") == 1
        assert tools.get("get_job_detail") == 1

    async def test_days_clamped(self):
        from bzp_workbench.db import SessionLocal
        from bzp_workbench.services.ai.analytics import usage_report

        async with SessionLocal() as db:
            report = await usage_report(db, days=9999)
        assert report["days"] <= 180

    async def test_no_billing_vocabulary(self):
        """**合规回归测试**：这是一句硬约束，必须守住。

        输出里不能出现额度、计费、套餐、充值、付费、会员等字眼 ——
        本工具是纯个人求职辅助，不存在任何消费功能。
        """
        import json

        from bzp_workbench.db import SessionLocal
        from bzp_workbench.services.ai.analytics import usage_report

        await seed_conversation()
        async with SessionLocal() as db:
            report = await usage_report(db)

        blob = json.dumps(report, ensure_ascii=False)
        for forbidden in ("额度", "计费", "套餐", "充值", "付费", "会员", "价格"):
            assert forbidden not in blob, f"用量报告里不该出现「{forbidden}」"

        # 字段名也不该带金额含义
        for key in report["totals"]:
            assert "cost" not in key.lower()
            assert "price" not in key.lower()
            assert "balance" not in key.lower()


# ============================================================
# 对话搜索
# ============================================================
class TestConversationSearch:
    async def test_search_by_title(self):
        from bzp_workbench.db import SessionLocal
        from bzp_workbench.services.ai import service as chat_service

        await seed_conversation(title="Docker 部署问题", user_text="无关内容")
        await seed_conversation(title="其他话题", user_text="也无关")

        async with SessionLocal() as db:
            rows = await chat_service.list_conversations(db, query="Docker")
        assert len(rows) == 1
        assert rows[0].title == "Docker 部署问题"

    async def test_search_by_message_content(self):
        """**关键能力**：标题里没有的词，也要能在消息内容里找到。

        用户往往记得"我问过 Docker 那个对话"，而标题是自动生成的首条
        消息前 30 字，未必包含他记得的那个词。
        """
        from bzp_workbench.db import SessionLocal
        from bzp_workbench.services.ai import service as chat_service

        await seed_conversation(title="随便的标题", user_text="帮我看看 Kubernetes 的事")
        await seed_conversation(title="另一个", user_text="完全无关")

        async with SessionLocal() as db:
            rows = await chat_service.list_conversations(db, query="Kubernetes")
        assert len(rows) == 1
        assert rows[0].title == "随便的标题"

    async def test_empty_query_returns_all(self):
        from bzp_workbench.db import SessionLocal
        from bzp_workbench.services.ai import service as chat_service

        await seed_conversation(title="A")
        await seed_conversation(title="B")
        async with SessionLocal() as db:
            rows = await chat_service.list_conversations(db, query="")
            assert len(rows) == 2
            rows = await chat_service.list_conversations(db, query="   ")
            assert len(rows) == 2, "纯空格应视为无查询"

    async def test_no_match_returns_empty(self):
        from bzp_workbench.db import SessionLocal
        from bzp_workbench.services.ai import service as chat_service

        await seed_conversation(title="A")
        async with SessionLocal() as db:
            rows = await chat_service.list_conversations(db, query="绝不可能匹配的词")
        assert rows == []

    async def test_search_does_not_duplicate(self):
        """标题和消息都命中时，同一条会话只能出现一次（用了 IN 子查询）。"""
        from bzp_workbench.db import SessionLocal
        from bzp_workbench.services.ai import service as chat_service

        await seed_conversation(title="Docker 话题", user_text="Docker 怎么部署")
        async with SessionLocal() as db:
            rows = await chat_service.list_conversations(db, query="Docker")
        assert len(rows) == 1


# ============================================================
# API 层
# ============================================================
class TestP6Api:
    async def test_usage_endpoint(self, client):
        response = await client.get("/api/chat/usage")
        assert response.status_code == 200
        data = response.json()["data"]
        assert "totals" in data
        assert "by_model" in data

    async def test_usage_days_param_validated(self, client):
        assert (await client.get("/api/chat/usage?days=0")).status_code == 422
        assert (await client.get("/api/chat/usage?days=9999")).status_code == 422

    async def test_search_endpoint(self, client):
        await seed_conversation(title="可搜索的对话")
        response = await client.get("/api/chat/conversations?q=可搜索")
        assert response.status_code == 200
        rows = response.json()["data"]
        assert len(rows) == 1

    async def test_search_query_length_validated(self, client):
        too_long = "x" * 200
        assert (await client.get(f"/api/chat/conversations?q={too_long}")).status_code == 422
