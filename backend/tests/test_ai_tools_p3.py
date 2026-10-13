"""测试：P3 能力（JD 全文读取 / 对话级任务进度推送）。

重点覆盖两类容易出错的地方：

* ``get_job_detail``：编号解析、JD 缺失时的诚实回退、截断、模式可见性
* ``chat_events``：**只有本会话的任务才该被推送** —— 这是这块最容易写错的
  地方，一旦搞错，用户开两个对话会同时收到不相干的任务消息
"""

from __future__ import annotations

import asyncio

import pytest

from bzp_workbench.enums import TaskStatus
from bzp_workbench.models import JobRecord, ScrapeTask
from bzp_workbench.services.ai.chat_events import (
    ChatEventHub,
    _FORWARDED_EVENTS,
)
from bzp_workbench.services.ai.tools import (
    GetJobDetailTool,
    ToolContext,
    ToolError,
    get_tool,
    tool_names,
    tools_for_mode,
)
from bzp_workbench.services.ai.tools.job_detail import MAX_JD_CHARS


def make_context(db) -> ToolContext:
    return ToolContext(db=db, conversation_id=1, model_config_id=1)


@pytest.fixture(autouse=True)
async def _clean_pytest_data():
    """清理测试数据。"""
    from sqlalchemy import delete

    from bzp_workbench.db import SessionLocal, init_db

    await init_db()
    async with SessionLocal() as session:
        await session.execute(delete(JobRecord).where(JobRecord.job_id.like("pytest-%")))
        await session.execute(delete(ScrapeTask).where(ScrapeTask.keyword.like("pytest%")))
        await session.commit()
    yield
    async with SessionLocal() as session:
        await session.execute(delete(JobRecord).where(JobRecord.job_id.like("pytest-%")))
        await session.execute(delete(ScrapeTask).where(ScrapeTask.keyword.like("pytest%")))
        await session.commit()


async def _make_job(**overrides) -> int:
    """造一条岗位记录，返回本地 id。"""
    from bzp_workbench.db import SessionLocal

    defaults = {
        "job_id": "pytest-job-detail",
        "title": "Python 后端工程师",
        "company": "示例科技",
        "salary": "20-40K",
        "city": "深圳",
        "location": "深圳·南山区",
        "experience": "3-5年",
        "degree": "本科",
        "job_link": "https://www.zhipin.com/job_detail/pytest-job-detail.html",
        "job_duty": "1. 负责后端服务的设计与开发\n2. 参与系统架构优化",
        "job_requirement": "1. 三年以上 Python 经验\n2. 熟悉 FastAPI / SQLAlchemy",
        "jd": "岗位职责与任职要求如上。",
    }
    defaults.update(overrides)
    async with SessionLocal() as session:
        row = JobRecord(**defaults)
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id


# ============================================================
# 工具注册
# ============================================================
class TestJobDetailRegistry:
    def test_registered(self):
        assert "get_job_detail" in tool_names()
        assert get_tool("get_job_detail") is not None

    def test_available_in_all_modes(self):
        """读 JD 对三种模式都有用 —— 面试辅导尤其需要它。"""
        for mode in ("job_hunting", "interview_coach", "chat"):
            assert "get_job_detail" in {t.name for t in tools_for_mode(mode)}

    def test_definition_shape(self):
        definition = GetJobDetailTool().to_definition()
        assert definition.name == "get_job_detail"
        assert "local_id" in definition.parameters["properties"]
        assert "job_id" in definition.parameters["properties"]


# ============================================================
# get_job_detail 行为
# ============================================================
class TestGetJobDetail:
    async def test_requires_an_identifier(self):
        from bzp_workbench.db import SessionLocal

        async with SessionLocal() as db:
            with pytest.raises(ToolError) as excinfo:
                await GetJobDetailTool().run({}, make_context(db))
        assert "local_id 或 job_id" in excinfo.value.message
        # 建议必须告诉模型下一步怎么做
        assert "search_existing_jobs" in (excinfo.value.hint or "")

    async def test_by_local_id(self):
        from bzp_workbench.db import SessionLocal

        job_pk = await _make_job()
        async with SessionLocal() as db:
            result = await GetJobDetailTool().run(
                {"local_id": job_pk}, make_context(db)
            )
        assert result.payload_type == "job_detail"
        assert result.data["title"] == "Python 后端工程师"
        assert result.data["has_detail"] is True
        # 模型文本要包含 JD 原文，否则模型没法分析
        assert "负责后端服务的设计与开发" in result.model_text
        assert "三年以上 Python 经验" in result.model_text

    async def test_by_boss_job_id(self):
        from bzp_workbench.db import SessionLocal

        await _make_job(job_id="pytest-by-biz-id")
        async with SessionLocal() as db:
            result = await GetJobDetailTool().run(
                {"job_id": "pytest-by-biz-id"}, make_context(db)
            )
        assert result.data["job_id"] == "pytest-by-biz-id"

    async def test_unknown_id_is_not_an_error(self):
        """查不到要如实说，而不是抛错中断对话。"""
        from bzp_workbench.db import SessionLocal

        async with SessionLocal() as db:
            result = await GetJobDetailTool().run(
                {"local_id": 99999999}, make_context(db)
            )
        assert result.payload_type == "text"
        assert "没有找到" in result.model_text
        assert "search_existing_jobs" in result.model_text

    async def test_missing_jd_returns_honest_fallback(self):
        """没有 JD 详情时必须明确告知，并禁止模型编造职责。

        这是最重要的一条：抓取时可能关掉了详情，模型很容易凭标题瞎编 JD。
        """
        from bzp_workbench.db import SessionLocal

        job_pk = await _make_job(
            job_id="pytest-no-jd", jd=None, job_duty=None, job_requirement=None
        )
        async with SessionLocal() as db:
            result = await GetJobDetailTool().run(
                {"local_id": job_pk}, make_context(db)
            )
        assert result.data["has_detail"] is False
        assert "还没有抓取 JD 详情" in result.model_text
        # 必须明确禁止编造
        assert "不要" in result.model_text
        assert "编造" in result.model_text

    async def test_long_jd_is_truncated(self):
        from bzp_workbench.db import SessionLocal

        job_pk = await _make_job(
            job_id="pytest-long-jd",
            # 明确超过上限（不多不少正好等于上限时不该标记为截断）
            job_duty="职责" * (MAX_JD_CHARS // 2 + 200),
            job_requirement=None,
            jd=None,
        )
        async with SessionLocal() as db:
            result = await GetJobDetailTool().run(
                {"local_id": job_pk}, make_context(db)
            )
        assert result.data["truncated"] is True
        # 必须告知被截断，否则模型会以为这就是全部
        assert "截断" in result.model_text
        # 前端拿到的文本也不能超上限 —— 早先只截断了 model_text，
        # payload 里仍是全文，卡片会被撑到几屏高
        assert len(result.data["jd"]) <= MAX_JD_CHARS

    async def test_exact_limit_is_not_marked_truncated(self):
        """正好等于上限时不需要截断，也不该吓唬模型说被截断了。"""
        from bzp_workbench.db import SessionLocal

        job_pk = await _make_job(
            job_id="pytest-exact-jd",
            job_duty="A" * MAX_JD_CHARS,
            job_requirement=None,
            jd=None,
        )
        async with SessionLocal() as db:
            result = await GetJobDetailTool().run(
                {"local_id": job_pk}, make_context(db)
            )
        assert result.data["truncated"] is False
        assert "截断" not in result.model_text

    async def test_model_text_includes_link(self):
        """模型需要真实链接来回答「给我链接」，否则会编造。"""
        from bzp_workbench.db import SessionLocal

        job_pk = await _make_job()
        async with SessionLocal() as db:
            result = await GetJobDetailTool().run(
                {"local_id": job_pk}, make_context(db)
            )
        assert "https://www.zhipin.com/job_detail/pytest-job-detail.html" in result.model_text
        assert "不要编造" in result.model_text

    async def test_payload_is_json_serializable(self):
        import json

        from bzp_workbench.db import SessionLocal

        job_pk = await _make_job()
        async with SessionLocal() as db:
            result = await GetJobDetailTool().run(
                {"local_id": job_pk}, make_context(db)
            )
        json.dumps(result.data)  # 要存进 content_json


# ============================================================
# 对话级推送
# ============================================================
class TestChatEventHubRouting:
    """核心：只有本会话的任务才该被推送。"""

    async def test_task_from_other_conversation_is_not_pushed(self):
        """别的对话发起的任务不能推给我 —— 这是最容易写错的地方。"""
        hub = ChatEventHub()
        my_queue = await hub.subscribe(1)
        await hub.subscribe(2)

        # 任务属于会话 2
        hub._task_conversation[100] = 2
        await hub._handle({"type": "task.progress", "data": {"task_id": 100}})

        assert my_queue.empty(), "会话 1 不该收到会话 2 的任务"

    async def test_task_without_conversation_is_not_pushed(self):
        """从「创建抓取任务」页发起的任务不属于任何对话。"""
        from bzp_workbench.db import SessionLocal

        async with SessionLocal() as session:
            task = ScrapeTask(
                keyword="pytest-no-conv",
                city="深圳",
                status=TaskStatus.RUNNING.value,
                conversation_id=None,
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)
            task_id = task.id

        hub = ChatEventHub()
        queue = await hub.subscribe(1)
        await hub._handle({"type": "task.progress", "data": {"task_id": task_id}})
        assert queue.empty()
        # 负结果不该进缓存 —— 否则之后补写归属也不会生效
        assert task_id not in hub._task_conversation

    async def test_late_conversation_assignment_is_picked_up(self):
        """**回归测试**：先无归属、后补写归属，必须能推出去。

        早先的实现把 ``None`` 也缓存了，于是首次查到"无归属"后就**永久屏蔽**
        了这个任务 —— 之后无论怎么补写 ``conversation_id`` 都收不到推送，
        而日志里显示 `归属会话=None`，极容易误判成路由逻辑写错。

        真实场景：应用层先建任务、稍后再补写归属；或任务号被复用
        （测试反复建删、数据库重置）。
        """
        from bzp_workbench.db import SessionLocal

        async with SessionLocal() as session:
            task = ScrapeTask(
                keyword="pytest-late-conv",
                city="深圳",
                status=TaskStatus.RUNNING.value,
                conversation_id=None,
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)
            task_id = task.id

        hub = ChatEventHub()
        queue = await hub.subscribe(42)

        # 第一次：无归属 → 不推、不缓存
        await hub._handle({"type": "task.progress", "data": {"task_id": task_id}})
        assert queue.empty()

        # 补写归属
        async with SessionLocal() as session:
            row = await session.get(ScrapeTask, task_id)
            row.conversation_id = 42
            await session.commit()

        # 第二次：必须能推出去（这曾经是坏的）
        await hub._handle({"type": "task.started", "data": {"task_id": task_id}})
        assert not queue.empty(), "补写归属后应当能推送 —— 负结果不该被缓存"
        payload = queue.get_nowait()
        assert payload["data"]["conversation_id"] == 42

    async def test_irrelevant_event_types_dropped(self):
        hub = ChatEventHub()
        queue = await hub.subscribe(1)
        hub._task_conversation[300] = 1
        for event_type in ("queue.updated", "queue.delay", "browser.starting"):
            await hub._handle({"type": event_type, "data": {"task_id": 300}})
        assert queue.empty(), "队列类事件与对话无关，不该转发"

    async def test_forwarded_event_types_include_lifecycle(self):
        """任务生命周期事件必须都在白名单里，否则用户看不到状态变化。"""
        for event_type in ("task.created", "task.started", "task.finished"):
            assert event_type in _FORWARDED_EVENTS

    async def test_no_subscriber_skips_database_lookup(self):
        """没人在听时不该查库 —— 这是省开销的关键路径。"""
        hub = ChatEventHub()
        hub._task_conversation[400] = 1
        # 没有订阅者：应该直接返回，不抛异常
        await hub._handle(
            {"type": "task.finished", "data": {"task_id": 400}}
        )
        # 终止事件后会清缓存
        assert 400 not in hub._task_conversation


class TestChatEventHubThrottle:
    async def test_progress_is_throttled_but_lifecycle_is_not(self):
        hub = ChatEventHub()
        # 第一次进度事件应通过
        assert hub._should_push(1, "task.progress") is True
        # 紧跟着的第二次应被节流
        assert hub._should_push(1, "task.progress") is False
        # 但状态变化不节流 —— 用户必须立刻看到"完成了"
        assert hub._should_push(1, "task.finished") is True

    async def test_created_and_started_not_throttled(self):
        hub = ChatEventHub()
        assert hub._should_push(2, "task.created") is True
        assert hub._should_push(2, "task.started") is True


class TestChatEventHubSubscription:
    async def test_subscribe_unsubscribe(self):
        hub = ChatEventHub()
        queue = await hub.subscribe(1)
        assert hub.subscriber_count == 1
        await hub.unsubscribe(1, queue)
        assert hub.subscriber_count == 0

    async def test_broadcast_reaches_all_connections_of_conversation(self):
        """同一会话开多个标签页时，每个都要收到。"""
        hub = ChatEventHub()
        q1 = await hub.subscribe(7)
        q2 = await hub.subscribe(7)
        await hub._broadcast(7, {"type": "ping"})
        assert q1.qsize() == 1
        assert q2.qsize() == 1

    async def test_slow_client_drops_oldest(self):
        """慢客户端不能让服务端内存无限增长：丢最旧的，保留最新状态。"""
        hub = ChatEventHub()
        queue = await hub.subscribe(9)
        # 塞满队列
        for index in range(queue.maxsize):
            await hub._broadcast(9, {"n": index})
        assert queue.full()
        await hub._broadcast(9, {"n": "newest"})
        # 队列仍然满载，但最新一条一定在
        assert queue.qsize() == queue.maxsize
        items = []
        while not queue.empty():
            items.append(queue.get_nowait())
        assert items[-1] == {"n": "newest"}


class TestChatEventHubRelay:
    async def test_relay_forwards_to_subscriber(self):
        """端到端：投递到全局总线 → 中继 → 会话订阅者。"""
        from bzp_workbench.services.events import task_events

        # 造一个真实任务（带 conversation_id）
        from bzp_workbench.db import SessionLocal

        async with SessionLocal() as session:
            task = ScrapeTask(
                keyword="pytest-relay",
                city="深圳",
                status=TaskStatus.RUNNING.value,
                progress=42,
                stage_text="抓取中",
                conversation_id=555,
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)
            task_id = task.id

        hub = ChatEventHub()
        queue = await hub.subscribe(555)
        hub.start_relay()
        try:
            # 等中继订阅到总线
            await asyncio.sleep(0.1)
            task_events.publish("task.progress", {"task_id": task_id})

            payload = await asyncio.wait_for(queue.get(), timeout=5)
            assert payload["type"] == "chat.task_progress"
            data = payload["data"]
            assert data["conversation_id"] == 555
            assert data["task"]["task_id"] == task_id
            assert data["task"]["progress"] == 42
            assert data["task"]["status_label"] == "进行中"
        finally:
            await hub.stop_relay()

    async def test_relay_survives_bad_event(self):
        """单个坏事件不能让中继退出 —— 否则之后所有推送都静默失效。"""
        from bzp_workbench.services.events import task_events

        hub = ChatEventHub()
        queue = await hub.subscribe(666)
        hub.start_relay()
        try:
            await asyncio.sleep(0.1)
            # 这条事件没有 task_id，会让处理逻辑提前返回（而不是崩）
            task_events.publish("task.progress", {})
            # 中继应仍然活着
            assert hub._relay_task is not None
            assert not hub._relay_task.done()

            # 确认还能正常转发下一条
            from bzp_workbench.db import SessionLocal

            async with SessionLocal() as session:
                task = ScrapeTask(
                    keyword="pytest-relay2",
                    city="深圳",
                    status=TaskStatus.SUCCEEDED.value,
                    conversation_id=666,
                    total_found=10,
                )
                session.add(task)
                await session.commit()
                await session.refresh(task)
                task_id = task.id

            task_events.publish("task.finished", {"task_id": task_id})
            payload = await asyncio.wait_for(queue.get(), timeout=5)
            assert payload["data"]["task"]["total_found"] == 10
        finally:
            await hub.stop_relay()

    async def test_start_relay_is_idempotent(self):
        hub = ChatEventHub()
        hub.start_relay()
        first = hub._relay_task
        hub.start_relay()
        assert hub._relay_task is first, "重复调用不应起第二个中继"
        await hub.stop_relay()
