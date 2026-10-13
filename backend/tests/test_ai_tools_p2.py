"""测试：P2 工具（发起抓取 / 查询任务进度）与薪资档位映射。

重点覆盖那些**会造成真实损失**的路径：

* 抓取工具会真的起一个 10-20 分钟的后台进程，所以「该拦住的必须拦住」：
  缺参数、未登录、超页数、重复提交
* 薪资档位映射必须诚实：BOSS 没有精确区间，映射偏宽时必须给出说明，
  否则用户会疑惑"为什么给我 45K 的岗位"
"""

from __future__ import annotations

import pytest

from bzp_workbench.enums import AuthStatus, TaskStatus
from bzp_workbench.services.ai.salary import (
    SALARY_BUCKETS,
    label_for,
    resolve_salary,
)
from bzp_workbench.services.ai.tools import (
    CheckTaskStatusTool,
    ToolContext,
    ToolError,
    TriggerBossScraperTool,
    get_tool,
    tools_for_mode,
)


# ============================================================
# 薪资档位映射
# ============================================================
class TestSalaryResolution:
    @pytest.mark.parametrize(
        ("raw", "code"),
        [
            ("15-25K", "405"),      # 按 15 落档（保证覆盖区间起点）
            ("15k-25k", "405"),     # 小写
            ("15~25K", "405"),      # 波浪号
            ("15至25K", "405"),     # 中文
            ("20-50K", "406"),      # 正好落在档位上
            ("50K以上", "407"),
            ("50K+", "407"),
            ("10K以下", "404"),
            ("3K以下", "402"),
            ("20K", "406"),         # 单个数字当下限
        ],
    )
    def test_parses_various_formats(self, raw, code):
        assert resolve_salary(raw).code == code

    def test_exact_bucket_has_no_note(self):
        """正好落在档位上时不应产生"偏宽"提示 —— 否则提示会变得廉价。"""
        assert resolve_salary("20-50K").note is None

    def test_wider_bucket_produces_honest_note(self):
        """映射偏宽时必须说明清楚：落到哪个档、会多出什么。"""
        result = resolve_salary("15-25K")
        assert result.code == "405"       # 10-20K
        assert result.note is not None
        # 必须说清两件事：实际落到哪个档、以及因此会多出什么
        assert "10-20K" in result.note
        assert "低于 15K" in result.note
        # 不能假装精确匹配
        assert "没有精确区间" in result.note

    def test_unlimited_when_absent(self):
        assert resolve_salary(None).code == "0"
        assert resolve_salary("").code == "0"
        assert resolve_salary(None).is_unlimited

    def test_unparseable_falls_back_to_unlimited(self):
        """解析失败不能报错 —— 薪资是选填项，不该因此让抓取失败。"""
        result = resolve_salary("面议")
        assert result.code == "0"
        assert result.note is not None
        assert "面议" in result.note

    def test_model_text_includes_bucket(self):
        text = resolve_salary("15-25K").to_model_text()
        assert "10-20K" in text
        assert "注意" in text

    def test_payload_is_serializable(self):
        import json

        payload = resolve_salary("15-25K").to_payload()
        assert payload["code"] == "405"
        assert payload["wanted"] == [15, 25]
        json.dumps(payload)  # 必须能 JSON 序列化（要存进 content_json）

    def test_label_for_unknown_code(self):
        assert label_for("999") == "999"
        assert label_for(None) == SALARY_BUCKETS["0"]

    def test_all_codes_present(self):
        for code in ("0", "402", "403", "404", "405", "406", "407"):
            assert code in SALARY_BUCKETS


# ============================================================
# 工具注册
# ============================================================
class TestP2ToolRegistry:
    def test_scraper_tool_registered(self):
        assert get_tool("trigger_boss_scraper") is not None
        assert get_tool("check_task_status") is not None

    def test_scraper_tool_only_in_job_hunting(self):
        """抓取是有副作用的重操作，不该在通用对话里被误调用。"""
        hunting = {t.name for t in tools_for_mode("job_hunting")}
        chat = {t.name for t in tools_for_mode("chat")}
        assert "trigger_boss_scraper" in hunting
        assert "trigger_boss_scraper" not in chat, "闲聊模式不应暴露抓取工具"

    def test_scraper_definition_shape(self):
        definition = TriggerBossScraperTool().to_definition()
        assert definition.name == "trigger_boss_scraper"
        assert definition.parameters["required"] == ["keyword", "city"]
        assert "max_pages" in definition.parameters["properties"]


# ============================================================
# 发起抓取任务
# ============================================================
@pytest.fixture
async def active_profile():
    """确保存在一个 active 的授权档案，否则抓取工具会（正确地）拒绝执行。"""
    from sqlalchemy import select

    from bzp_workbench.config import settings
    from bzp_workbench.db import SessionLocal
    from bzp_workbench.models import AuthProfile

    await _cleanup()
    async with SessionLocal() as session:
        row = (
            await session.execute(select(AuthProfile).limit(1))
        ).scalars().first()
        if row is None:
            row = AuthProfile(
                name="pytest 档案",
                browser="chrome",
                cdp_port=9222,
                profile_hash="pytest-hash",
                profile_dir=str(settings.profile_dir("pytest-hash")),
                status=AuthStatus.ACTIVE.value,
            )
            session.add(row)
        else:
            row.status = AuthStatus.ACTIVE.value
        await session.commit()
        await session.refresh(row)
        return row.id


async def _cleanup():
    """清掉测试造出来的任务。

    为什么必须连"取消"一起做
    ------------------------
    ``TriggerBossScraperTool`` 会真的调 ``task_queue.notify()``。测试造出来的
    任务如果只删记录不取消，后台 worker 仍会尝试执行它（并在真实环境里试图
    启动 Chrome），进而干扰其它测试 —— 表现为偶发的计数不符，很难定位。
    所以这里先把未结束的任务置为 cancelled，再删除测试数据。
    """
    from sqlalchemy import delete, select, update

    from bzp_workbench.db import SessionLocal, init_db
    from bzp_workbench.models import ScrapeTask

    # 必须先建表：本文件可能先于其它用 client 夹具的测试运行
    await init_db()
    active = (TaskStatus.PENDING.value, TaskStatus.RUNNING.value)
    async with SessionLocal() as session:
        # 1) 先把所有未结束的任务取消掉，避免后台真的去跑它们
        await session.execute(
            update(ScrapeTask)
            .where(ScrapeTask.status.in_(active))
            .values(status=TaskStatus.CANCELLED.value, stage_text="测试清理")
        )
        # 2) 再删掉测试造的数据
        await session.execute(delete(ScrapeTask).where(ScrapeTask.keyword.like("pytest%")))
        await session.commit()


@pytest.fixture(autouse=True)
async def _clean_pytest_tasks():
    await _cleanup()
    yield
    await _cleanup()


def make_context(db) -> ToolContext:
    return ToolContext(db=db, conversation_id=1, model_config_id=1)


class TestTriggerScraperValidation:
    async def test_requires_keyword_and_city(self, active_profile):
        from bzp_workbench.db import SessionLocal

        async with SessionLocal() as db:
            with pytest.raises(ToolError) as excinfo:
                await TriggerBossScraperTool().run({"keyword": "pytest-only"}, make_context(db))
        assert "必填" in excinfo.value.message
        # 错误必须给出可操作建议：让模型去问用户，而不是自己编一个城市
        assert "确认" in (excinfo.value.hint or "")

    async def test_rejects_when_not_logged_in(self):
        """登录态失效时必须当场拒绝。

        真实抓取在未登录下必然失败，让用户等 15 分钟再看到失败是糟糕体验。
        """
        from sqlalchemy import select

        from bzp_workbench.db import SessionLocal
        from bzp_workbench.models import AuthProfile

        async with SessionLocal() as db:
            row = (await db.execute(select(AuthProfile).limit(1))).scalars().first()
            original = row.status if row else None
            if row is None:
                pytest.skip("需要至少一个授权档案才能测这个分支")
            row.status = AuthStatus.EXPIRED.value
            await db.commit()
            try:
                with pytest.raises(ToolError) as excinfo:
                    await TriggerBossScraperTool().run(
                        {"keyword": "pytest-a", "city": "深圳"}, make_context(db)
                    )
                assert "登录态" in excinfo.value.message
                assert "扫码" in (excinfo.value.hint or "")
            finally:
                row.status = original or AuthStatus.ACTIVE.value
                await db.commit()


class TestTriggerScraperExecution:
    async def test_creates_task_and_returns_card(self, active_profile):
        from bzp_workbench.db import SessionLocal

        async with SessionLocal() as db:
            result = await TriggerBossScraperTool().run(
                {
                    "keyword": "pytest-python",
                    "city": "深圳市",           # 带「市」应被归一化
                    "salary": "15-25K",
                    "experience": "3-5年",
                    "max_pages": 2,
                    "with_detail": False,
                },
                make_context(db),
            )

        assert result.payload_type == "task_created"
        data = result.data
        assert data["task_id"] > 0
        assert data["city"] == "深圳", "城市名应被归一化"
        assert data["status"] == TaskStatus.PENDING.value
        assert data["max_pages"] == 2
        assert data["with_detail"] is False
        # 薪资档位要带说明（15 落 10-20K 档）
        assert data["salary"]["code"] == "405"
        assert data["salary"]["note"]
        # 必须告知耗时，否则用户以为点一下就好
        assert data["estimated_minutes"] > 0

    async def test_model_text_forbids_claiming_results(self, active_profile):
        """模型文本必须明确禁止"我已经抓到数据了"这种谎报。"""
        from bzp_workbench.db import SessionLocal

        async with SessionLocal() as db:
            result = await TriggerBossScraperTool().run(
                {"keyword": "pytest-text", "city": "深圳"}, make_context(db)
            )
        assert "不要" in result.model_text
        assert "还没跑完" in result.model_text or "异步" in result.model_text
        # 涉及薪资档位差异时要提示模型如实转达
        assert "薪资" in result.model_text

    async def test_pages_are_capped(self, active_profile):
        """模型可能说"我抓 50 页"，必须收紧并如实告知。"""
        from bzp_workbench.db import SessionLocal
        from bzp_workbench.services.ai.tools.trigger_scraper import MAX_PAGES

        async with SessionLocal() as db:
            result = await TriggerBossScraperTool().run(
                {"keyword": "pytest-cap", "city": "深圳", "max_pages": 999},
                make_context(db),
            )
        assert result.data["max_pages"] == MAX_PAGES
        assert "上限" in result.model_text, "收紧页数必须告知模型，让它转达用户"

    async def test_duplicate_active_task_is_not_recreated(self, active_profile):
        """相同条件的活跃任务不重复创建 —— 否则白跑一遍还可能触发风控。"""
        from bzp_workbench.db import SessionLocal

        args = {"keyword": "pytest-dup", "city": "深圳"}
        async with SessionLocal() as db:
            first = await TriggerBossScraperTool().run(args, make_context(db))
            second = await TriggerBossScraperTool().run(args, make_context(db))

        assert first.data["task_id"] == second.data["task_id"], "不应创建第二个任务"
        assert second.data.get("duplicate") is True
        assert "没有重复创建" in second.model_text

    async def test_different_city_creates_new_task(self, active_profile):
        """城市不同就是不同任务，不能被去重误伤。"""
        from bzp_workbench.db import SessionLocal

        async with SessionLocal() as db:
            first = await TriggerBossScraperTool().run(
                {"keyword": "pytest-city", "city": "深圳"}, make_context(db)
            )
            second = await TriggerBossScraperTool().run(
                {"keyword": "pytest-city", "city": "北京"}, make_context(db)
            )
        assert first.data["task_id"] != second.data["task_id"]

    async def test_unlimited_salary_not_sent_as_filter(self, active_profile):
        """「不限」不能当成筛选条件传下去（否则会滤掉全部结果）。"""
        from bzp_workbench.db import SessionLocal

        async with SessionLocal() as db:
            result = await TriggerBossScraperTool().run(
                {"keyword": "pytest-nosal", "city": "深圳"}, make_context(db)
            )
            task = await _get_task(db, result.data["task_id"])
        assert result.data["salary"]["code"] == "0"
        assert task.salary is None, "不限薪资不应写入筛选条件"

    async def test_option_labels_mapped(self, active_profile):
        """经验/学历的中文标签要映射成爬虫档位码。"""
        from bzp_workbench.db import SessionLocal

        async with SessionLocal() as db:
            result = await TriggerBossScraperTool().run(
                {
                    "keyword": "pytest-opt",
                    "city": "深圳",
                    "experience": "3-5年",
                    "degree": "本科",
                },
                make_context(db),
            )
            task = await _get_task(db, result.data["task_id"])
        assert task.experience == "105"
        assert task.degree == "205"

    async def test_unlimited_option_becomes_none(self, active_profile):
        from bzp_workbench.db import SessionLocal

        async with SessionLocal() as db:
            result = await TriggerBossScraperTool().run(
                {"keyword": "pytest-unl", "city": "深圳", "experience": "不限"},
                make_context(db),
            )
            task = await _get_task(db, result.data["task_id"])
        assert task.experience is None


async def _get_task(db, task_id: int):
    from bzp_workbench.models import ScrapeTask

    return await db.get(ScrapeTask, task_id)


# ============================================================
# 查询任务进度
# ============================================================
class TestCheckTaskStatus:
    async def test_list_recent_tasks(self, active_profile):
        from bzp_workbench.db import SessionLocal

        async with SessionLocal() as db:
            # 先造一个任务
            created = await TriggerBossScraperTool().run(
                {"keyword": "pytest-status", "city": "深圳"}, make_context(db)
            )
            result = await CheckTaskStatusTool().run({}, make_context(db))

        assert result.payload_type == "task_status"
        tasks = result.data["tasks"]
        assert tasks
        assert any(item["task_id"] == created.data["task_id"] for item in tasks)
        assert tasks[0]["status_label"], "必须带中文状态标签"

    async def test_query_specific_task(self, active_profile):
        from bzp_workbench.db import SessionLocal

        async with SessionLocal() as db:
            created = await TriggerBossScraperTool().run(
                {"keyword": "pytest-one", "city": "深圳"}, make_context(db)
            )
            task_id = created.data["task_id"]
            result = await CheckTaskStatusTool().run({"task_id": task_id}, make_context(db))

        assert len(result.data["tasks"]) == 1
        assert result.data["tasks"][0]["task_id"] == task_id

    async def test_unknown_task_id_is_not_an_error(self):
        """查不到任务要如实说，而不是抛错中断对话。"""
        from bzp_workbench.db import SessionLocal

        async with SessionLocal() as db:
            result = await CheckTaskStatusTool().run(
                {"task_id": 99999999}, make_context(db)
            )
        assert result.payload_type == "text"
        assert "没有找到" in result.model_text

    async def test_limit_is_clamped(self, active_profile):
        from bzp_workbench.db import SessionLocal

        async with SessionLocal() as db:
            result = await CheckTaskStatusTool().run({"limit": 9999}, make_context(db))
        assert len(result.data["tasks"]) <= 10

    async def test_active_task_text_tells_model_not_to_overclaim(self, active_profile):
        """任务还在跑时必须提醒模型别说过头。"""
        from bzp_workbench.db import SessionLocal

        async with SessionLocal() as db:
            await TriggerBossScraperTool().run(
                {"keyword": "pytest-active", "city": "深圳"}, make_context(db)
            )
            result = await CheckTaskStatusTool().run({}, make_context(db))

        assert "进行中" in result.model_text
        assert "不要" in result.model_text
