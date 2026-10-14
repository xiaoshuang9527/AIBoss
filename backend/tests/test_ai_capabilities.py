"""测试：P1 能力层（工具、路由、Runner）。

覆盖 P1 引入的三块新逻辑，以及它们最容易出错的地方：

* **工具**：参数校验、空结果不报错、城市名归一化、返回结构
* **路由**：脏输出容错、显式切换优先、已有模式不重复调模型（成本控制）
* **Runner**：能力守卫、工具循环、熔断、去重、上下文裁剪
"""

from __future__ import annotations

import pytest

from bzp_workbench.services.ai.router import (
    MODES,
    detect_explicit_switch,
    parse_router_output,
    route,
)
from bzp_workbench.services.ai.runner import (
    MAX_TOOL_ROUNDS,
    TurnResult,
    guard_capabilities,
    trim_history,
)
from bzp_workbench.services.ai.tools import (
    Tool,
    ToolContext,
    ToolError,
    ToolResult,
    get_tool,
    tool_names,
    tools_for_mode,
)
from bzp_workbench.services.ai.tools.search_jobs import (
    SearchExistingJobsTool,
    _normalize_city,
)
from bzp_workbench.services.llm import (
    LLMError,
    LLMErrorType,
    Message,
    Role,
    StreamChunk,
    StreamEventType,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)


# ============================================================
# 测试替身
# ============================================================
class ScriptedAdapter:
    """按脚本逐轮产出 chunk 的假适配器。

    用它测试 Runner 的循环逻辑，不需要真实模型。每次 ``stream_chat`` 调用
    消费脚本里的一"轮"。
    """

    def __init__(self, rounds: list[dict], *, model: str = "scripted") -> None:
        self.rounds = rounds
        self.call_count = 0
        self.received_messages: list[list[Message]] = []
        self.received_tools: list[list[ToolDefinition] | None] = []
        self.model = model

    async def stream_chat(self, messages, *, tools=None, **kwargs):
        self.received_messages.append(list(messages))
        self.received_tools.append(list(tools) if tools else None)

        index = min(self.call_count, len(self.rounds) - 1)
        self.call_count += 1
        script = self.rounds[index] if self.rounds else {}

        for piece in script.get("reasoning", ""):
            yield StreamChunk(type=StreamEventType.REASONING, text=piece)
        for piece in script.get("text", ""):
            yield StreamChunk(type=StreamEventType.TEXT, text=piece)
        for call in script.get("tool_calls", []):
            yield StreamChunk(type=StreamEventType.TOOL_CALL, tool_call=call)

        usage = script.get("usage")
        if usage:
            yield StreamChunk(type=StreamEventType.USAGE, usage=usage)
        yield StreamChunk(
            type=StreamEventType.DONE, finish_reason=script.get("finish_reason", "stop")
        )

    async def chat_completion(self, messages, *, tools=None, **kwargs):
        """非流式入口 —— 意图路由用它。

        路由走的是 ``chat_completion`` 而不是 ``stream_chat``，
        测试替身两个都要实现，否则路由会静默回退到 chat。
        """
        from bzp_workbench.services.llm import LLMResponse

        self.received_messages.append(list(messages))
        self.received_tools.append(list(tools) if tools else None)

        index = min(self.call_count, len(self.rounds) - 1)
        self.call_count += 1
        script = self.rounds[index] if self.rounds else {}

        return LLMResponse(
            text=script.get("text", ""),
            reasoning=script.get("reasoning", ""),
            tool_calls=list(script.get("tool_calls", [])),
            finish_reason=script.get("finish_reason", "stop"),
            usage=script.get("usage") or TokenUsage(),
            model=self.model,
        )


async def collect_frames(gen) -> list[tuple[str, dict]]:
    frames = []
    async for frame in gen:
        frames.append((frame.event.value, frame.data))
    return frames


class _FakeDb:
    """工具测试用的占位数据库会话（search_jobs 会把它透传给 repository）。"""


# ============================================================
# 工具层
# ============================================================
class TestToolRegistry:
    def test_registered_tools(self):
        assert "search_existing_jobs" in tool_names()

    def test_interview_mode_has_no_job_tools(self):
        """面试辅导模式下不应暴露**发起抓取/检索岗位**这类工具。

        注意 ``get_job_detail`` 是刻意保留的：分析简历与岗位匹配度时，
        必须能读到目标岗位的 JD 全文。
        """
        coach_tools = {t.name for t in tools_for_mode("interview_coach")}
        # 这些会改动数据或消耗抓取资源，面试辅导模式下不该出现
        for forbidden in (
            "search_existing_jobs",
            "trigger_boss_scraper",
            "check_task_status",
        ):
            assert forbidden not in coach_tools, f"{forbidden} 不该在面试辅导模式下暴露"
        # 读 JD 是面试辅导的刚需
        assert "get_job_detail" in coach_tools

        hunting = {t.name for t in tools_for_mode("job_hunting")}
        assert "search_existing_jobs" in hunting

    def test_get_tool(self):
        assert get_tool("search_existing_jobs") is not None
        assert get_tool("not_a_tool") is None

    def test_definition_is_openai_shaped(self):
        definition = SearchExistingJobsTool().to_definition()
        assert definition.name == "search_existing_jobs"
        assert definition.parameters["type"] == "object"
        assert "keyword" in definition.parameters["properties"]


class TestCityNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("深圳", "深圳"),
            ("深圳市", "深圳"),
            ("  北京  ", "北京"),
            ("上海市", "上海"),
            ("赣州", "赣州"),
        ],
    )
    def test_normalize(self, raw, expected):
        assert _normalize_city(raw) == expected

    def test_does_not_strip_short_names(self):
        # 「市」本身不该被剥成空串
        assert _normalize_city("市") == "市"


class TestSearchJobsTool:
    async def test_requires_keyword_or_city(self):
        tool = SearchExistingJobsTool()
        context = ToolContext(db=_FakeDb(), conversation_id=1, model_config_id=1)
        with pytest.raises(ToolError) as excinfo:
            await tool.run({}, context)
        assert "至少" in excinfo.value.message
        # 错误要带上可操作的建议
        assert "确认" in (excinfo.value.hint or "")

    async def test_empty_result_is_not_an_error(self, monkeypatch):
        """查不到数据不是错误 —— 模型需要看到"0 条"才能决定是否发起抓取。"""
        tool = SearchExistingJobsTool()
        context = ToolContext(db=_FakeDb(), conversation_id=1, model_config_id=1)

        async def fake_list_jobs(*args, **kwargs):
            return [], 0

        monkeypatch.setattr(
            "bzp_workbench.services.ai.tools.search_jobs.repo.list_jobs", fake_list_jobs
        )
        result = await tool.run({"keyword": "Python", "city": "深圳"}, context)

        assert result.payload_type == "job_cards"
        assert result.data["total"] == 0
        # 模型文本里要提示可以改用抓取
        assert "trigger_boss_scraper" in result.model_text

    async def test_returns_cards_and_model_text(self, monkeypatch):
        tool = SearchExistingJobsTool()
        context = ToolContext(db=_FakeDb(), conversation_id=1, model_config_id=1)

        class Row:
            id = 1
            job_id = "abc"
            title = "Python 后端工程师"
            company = "示例科技"
            salary = "20-40K"
            salary_avg = 30
            city = "深圳"
            location = "深圳·南山区"
            experience = "3-5年"
            degree = "本科"
            company_scale = "500-999人"
            company_stage = "B轮"
            company_industry = "互联网"
            boss_name = "张三"
            boss_active_status = "刚刚活跃"
            skill_keywords = '["Python", "FastAPI"]'
            skill_tags = None
            welfare = "五险一金"
            jd = "岗位职责：负责后端开发"
            job_link = "https://www.zhipin.com/job_detail/abc.html"
            company_link = None
            is_favorite = False
            scraped_at = None

        async def fake_list_jobs(*args, **kwargs):
            return [Row()], 1

        monkeypatch.setattr(
            "bzp_workbench.services.ai.tools.search_jobs.repo.list_jobs", fake_list_jobs
        )
        result = await tool.run({"keyword": "Python", "city": "深圳"}, context)

        assert result.payload_type == "job_cards"
        card = result.data["jobs"][0]
        assert card["title"] == "Python 后端工程师"
        assert card["skills"] == ["Python", "FastAPI"]
        assert card["salary_bucket"]
        # 卡片必须带跳转链接，前端才能渲染「去 BOSS 沟通」
        assert card["job_link"].endswith("abc.html")
        # 模型文本必须包含链接，否则模型会编造 URL
        assert "https://www.zhipin.com/job_detail/abc.html" in result.model_text
        assert "不得自行编造" in result.model_text

    async def test_limit_is_clamped(self, monkeypatch):
        tool = SearchExistingJobsTool()
        context = ToolContext(db=_FakeDb(), conversation_id=1, model_config_id=1)
        captured: dict = {}

        async def fake_list_jobs(*args, **kwargs):
            captured.update(kwargs)
            return [], 0

        monkeypatch.setattr(
            "bzp_workbench.services.ai.tools.search_jobs.repo.list_jobs", fake_list_jobs
        )
        await tool.run({"keyword": "x", "limit": 9999}, context)
        assert captured["page_size"] <= 10


class TestToolError:
    def test_to_model_text_includes_hint(self):
        error = ToolError("查询失败", hint="稍后重试")
        text = error.to_model_text()
        assert "查询失败" in text
        assert "稍后重试" in text


# ============================================================
# 路由
# ============================================================
class TestRouterParsing:
    def test_standard_json(self):
        mode, reason, confidence = parse_router_output(
            '{"intent": "job_hunting", "reason": "提到岗位", "confidence": 0.9}'
        )
        assert mode == "job_hunting"
        assert confidence == pytest.approx(0.9)

    def test_markdown_wrapped(self):
        raw = '```json\n{"intent": "interview_coach", "confidence": 0.8}\n```'
        assert parse_router_output(raw)[0] == "interview_coach"

    def test_json_embedded_in_prose(self):
        raw = '根据分析，结果是 {"intent": "job_hunting"} 这样。'
        assert parse_router_output(raw)[0] == "job_hunting"

    def test_bare_mode_word(self):
        assert parse_router_output("我觉得是 job_hunting")[0] == "job_hunting"

    def test_invalid_mode_falls_back_to_chat(self):
        assert parse_router_output('{"intent": "not_a_mode"}')[0] == "chat"

    def test_garbage_falls_back_to_chat(self):
        assert parse_router_output("完全无法解析")[0] == "chat"

    def test_empty_falls_back_to_chat(self):
        assert parse_router_output("")[0] == "chat"

    def test_confidence_is_clamped(self):
        assert parse_router_output('{"intent":"chat","confidence":5}')[2] == 1.0
        assert parse_router_output('{"intent":"chat","confidence":-3}')[2] == 0.0


class TestExplicitSwitch:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("切换到找岗位", "job_hunting"),
            ("进入找工作模式", "job_hunting"),
            ("切换到面试辅导", "interview_coach"),
            ("换到闲聊模式", "chat"),
            ("深圳有什么岗位", None),
            ("解释一下 REST", None),
        ],
    )
    def test_detection(self, text, expected):
        assert detect_explicit_switch(text) == expected


class TestRoute:
    async def test_explicit_switch_skips_model(self):
        """用户显式切换时不该再问模型，省一次往返。"""
        adapter = ScriptedAdapter([{"text": "should not be called"}])
        decision = await route(adapter, "切换到找岗位", current_mode="chat")
        assert decision.mode == "job_hunting"
        assert decision.explicit is True
        assert adapter.call_count == 0

    async def test_keeps_mode_without_calling_model(self):
        """已有模式且不是首轮 → 沿用，不调模型（成本控制的关键）。"""
        adapter = ScriptedAdapter([{"text": "should not be called"}])
        decision = await route(adapter, "那薪资呢", current_mode="job_hunting")
        assert decision.mode == "job_hunting"
        assert adapter.call_count == 0
        assert decision.used_model is False

    async def test_first_turn_calls_model(self):
        adapter = ScriptedAdapter([{"text": '{"intent": "job_hunting", "confidence": 0.9}'}])
        decision = await route(adapter, "深圳有什么 Python 岗位", is_first_turn=True)
        assert decision.mode == "job_hunting"
        assert decision.used_model is True
        assert adapter.call_count == 1

    async def test_router_failure_falls_back(self):
        """路由失败不能拖垮对话：回退到 chat，用户仍能得到回复。"""

        class Broken:
            async def stream_chat(self, *args, **kwargs):
                raise RuntimeError("boom")
                yield  # pragma: no cover

            async def chat_completion(self, *args, **kwargs):
                raise LLMError(LLMErrorType.NETWORK_ERROR, "连接失败")

        decision = await route(Broken(), "随便说说", is_first_turn=True)
        assert decision.mode == "chat"
        assert "路由失败" in decision.reason

    async def test_unset_mode_on_first_turn_routes(self):
        adapter = ScriptedAdapter([{"text": '{"intent": "interview_coach"}'}])
        decision = await route(adapter, "帮我看看简历", current_mode="unset", is_first_turn=True)
        assert decision.mode == "interview_coach"


# ============================================================
# 能力守卫
# ============================================================
class TestCapabilityGuard:
    def test_passes_when_capable(self):
        guard_capabilities(["text", "vision", "tool_calls"], has_images=True, has_tools=True, model_label="m")

    def test_rejects_image_without_vision(self):
        with pytest.raises(LLMError) as excinfo:
            guard_capabilities(["text"], has_images=True, has_tools=False, model_label="慢模型")
        assert excinfo.value.error_type is LLMErrorType.UNSUPPORTED_CAPABILITY
        assert "看图" in excinfo.value.message
        # 提示要告诉用户怎么办
        assert "模型管理" in (excinfo.value.detail or "")

    def test_rejects_tools_without_tool_calls(self):
        with pytest.raises(LLMError) as excinfo:
            guard_capabilities(["text"], has_images=False, has_tools=True, model_label="m")
        assert "工具调用" in excinfo.value.message

    def test_no_guard_when_nothing_needed(self):
        guard_capabilities(["text"], has_images=False, has_tools=False, model_label="m")


# ============================================================
# 上下文裁剪
# ============================================================
class TestTrimHistory:
    def test_keeps_system_message(self):
        messages = [Message.system("系统提示")]
        messages += [Message.user(f"消息{i}") for i in range(60)]
        trimmed = trim_history(messages, limit=10)
        assert trimmed[0].role is Role.SYSTEM
        assert len(trimmed) <= 10
        # 保留的是最近的消息
        assert trimmed[-1].content == "消息59"

    def test_short_history_untouched(self):
        messages = [Message.system("s"), Message.user("u")]
        assert trim_history(messages, limit=10) == messages

    def test_all_system_messages(self):
        messages = [Message.system(f"s{i}") for i in range(5)]
        assert len(trim_history(messages, limit=10)) == 5


# ============================================================
# Runner：工具循环
# ============================================================
class TestRunnerToolLoop:
    async def _run(self, adapter, *, mode="job_hunting", capabilities=None, messages=None):
        from bzp_workbench.services.ai.runner import run_turn

        result = TurnResult()
        frames = await collect_frames(
            run_turn(
                adapter,
                messages=messages or [Message.system("sys"), Message.user("深圳 Python 岗位")],
                mode=mode,
                result=result,
                tool_context=ToolContext(db=_FakeDb(), conversation_id=1, model_config_id=1),
                model_label="测试模型",
                model_capabilities=capabilities or ["text", "tool_calls"],
            )
        )
        return frames, result

    async def test_simple_reply_without_tools(self):
        adapter = ScriptedAdapter([{"reasoning": "想想", "text": "你好", "usage": TokenUsage(10, 5)}])
        frames, result = await self._run(adapter)
        names = [name for name, _ in frames]
        assert "reasoning" in names and "text" in names
        assert result.text == "你好"
        assert result.reasoning == "想想"
        assert result.prompt_tokens == 10
        assert adapter.call_count == 1
        # run_turn 不产出 usage/done（由调用方负责）
        assert "usage" not in names and "done" not in names

    async def test_tool_call_then_final_text(self):
        """完整的一轮：模型要工具 → 执行 → 回填 → 模型给最终回答。"""
        adapter = ScriptedAdapter(
            [
                {"tool_calls": [ToolCall(id="c1", name="search_existing_jobs",
                                        arguments='{"keyword":"Python","city":"深圳"}')]},
                {"text": "找到了 3 个岗位"},
            ]
        )

        async def fake_run(self_tool, arguments, context):
            return ToolResult(
                payload_type="job_cards",
                data={"jobs": [{"title": "岗位A"}], "total": 1},
                model_text="命中 1 条",
            )

        import bzp_workbench.services.ai.runner as runner_module

        original = runner_module._execute_tool

        async def patched(name, arguments, context):
            if name == "search_existing_jobs":
                return await fake_run(None, arguments, context)
            return await original(name, arguments, context)

        runner_module._execute_tool = patched
        try:
            frames, result = await self._run(adapter)
        finally:
            runner_module._execute_tool = original

        names = [name for name, _ in frames]
        assert names.count("tool_call") == 1
        assert names.count("tool_result") == 1
        assert "text" in names
        assert result.text == "找到了 3 个岗位"
        assert len(result.tool_calls) == 1
        assert len(result.tool_results) == 1
        assert adapter.call_count == 2

        # 第二次调用时必须把工具结果作为 tool 消息回填
        second_round = adapter.received_messages[1]
        tool_messages = [m for m in second_round if m.role is Role.TOOL]
        assert len(tool_messages) == 1
        assert tool_messages[0].content == "命中 1 条"

    async def test_duplicate_tool_calls_are_skipped(self):
        """模型重复调用同一工具+参数时跳过，避免无意义循环。"""
        same = ToolCall(id="c1", name="search_existing_jobs", arguments='{"keyword":"x"}')
        adapter = ScriptedAdapter(
            [
                {"tool_calls": [same]},
                {"tool_calls": [ToolCall(id="c2", name="search_existing_jobs",
                                         arguments='{"keyword":"x"}')]},
                {"text": "结束"},
            ]
        )
        frames, result = await self._run(adapter)
        # 只应执行一次
        assert len(result.tool_results) == 1

    async def test_circuit_breaker_stops_after_max_rounds(self):
        """每轮都要求调工具：必须在 MAX_TOOL_ROUNDS 后强制收尾。"""
        rounds = [
            {"tool_calls": [ToolCall(id=f"c{i}", name="search_existing_jobs",
                                     arguments=f'{{"keyword":"k{i}"}}')]}
            for i in range(MAX_TOOL_ROUNDS + 5)
        ]
        adapter = ScriptedAdapter(rounds)
        frames, result = await self._run(adapter)

        # 调用次数 = 工具轮数 + 最后一次收尾（不带工具）
        assert adapter.call_count <= MAX_TOOL_ROUNDS + 1
        # 最后一轮不应把工具交给模型
        assert adapter.received_tools[-1] is None

    async def test_tool_failure_is_fed_back_not_raised(self):
        """工具失败要让模型知道原因，而不是中断对话。"""
        adapter = ScriptedAdapter(
            [
                {"tool_calls": [ToolCall(id="c1", name="search_existing_jobs",
                                         arguments="{}")]},
                {"text": "需要更多信息"},
            ]
        )
        frames, result = await self._run(adapter)
        assert result.text == "需要更多信息"
        tool_messages = [m for m in adapter.received_messages[1] if m.role is Role.TOOL]
        assert tool_messages and "失败" in tool_messages[0].content

    async def test_unknown_tool_is_handled(self):
        adapter = ScriptedAdapter(
            [
                {"tool_calls": [ToolCall(id="c1", name="no_such_tool", arguments="{}")]},
                {"text": "好的"},
            ]
        )
        frames, result = await self._run(adapter)
        tool_messages = [m for m in adapter.received_messages[1] if m.role is Role.TOOL]
        assert "不存在" in tool_messages[0].content

    async def test_capability_guard_blocks_before_request(self):
        """模型缺 vision 但用户传了图 → 本地拦截，不发请求。"""
        from bzp_workbench.services.llm import ImagePart

        adapter = ScriptedAdapter([{"text": "不该被调用"}])
        messages = [
            Message.system("sys"),
            Message.user("看这个", [ImagePart(data="x", mime_type="image/png")]),
        ]
        frames, result = await self._run(
            adapter, capabilities=["text", "tool_calls"], messages=messages
        )
        names = [name for name, _ in frames]
        assert "error" in names
        assert result.error_type == LLMErrorType.UNSUPPORTED_CAPABILITY.value
        # 关键：一次请求都没发出去
        assert adapter.call_count == 0

    async def test_interview_mode_only_gets_read_only_tools(self):
        """面试辅导模式下只应拿到**只读**工具，不该拿到抓取类工具。

        只读工具包括读 JD 与生成诊断报告 —— 两者都不改动数据、不消耗抓取资源。
        """
        adapter = ScriptedAdapter([{"text": "诊断报告"}])
        frames, result = await self._run(
            adapter,
            mode="interview_coach",
            # 有工具就必须声明 tool_calls 能力，否则会被能力守卫（正确地）拦下
            capabilities=["text", "tool_calls"],
        )
        tools = adapter.received_tools[0]
        assert tools is not None, "面试辅导需要工具，应传工具定义"
        names = {t.name for t in tools}
        assert names == {"get_job_detail", "analyze_match"}
        # 抓取类工具绝对不能出现
        assert "trigger_boss_scraper" not in names
        assert result.text == "诊断报告"

    async def test_tools_without_capability_are_blocked(self):
        """模型没声明 tool_calls 能力时，带工具的模式必须被拦下。

        这是能力守卫的回归测试：拦截发生在**发起请求之前**，
        否则用户会收到一个很难懂的 API 400。
        """
        adapter = ScriptedAdapter([{"text": "不该被调用"}])
        frames, result = await self._run(
            adapter, mode="interview_coach", capabilities=["text"]
        )
        names = [name for name, _ in frames]
        assert "error" in names
        assert result.error_type == "unsupported_capability"
        assert adapter.call_count == 0, "应在本地拦截，不发请求"

    async def test_usage_accumulates_across_rounds(self):
        """多轮时必须累加 token，否则只会记下最后一轮。"""
        adapter = ScriptedAdapter(
            [
                {"tool_calls": [ToolCall(id="c1", name="search_existing_jobs",
                                         arguments='{"keyword":"a"}')],
                 "usage": TokenUsage(100, 10)},
                {"text": "完成", "usage": TokenUsage(200, 20)},
            ]
        )
        _, result = await self._run(adapter)
        assert result.prompt_tokens == 300
        assert result.completion_tokens == 30


# ============================================================
# 端到端：假模型驱动走完整 Runner
# ============================================================
class TestFakeDriverThroughRunner:
    async def test_fake_driver_job_query_executes_tool(self, monkeypatch):
        """假模型会发起 search_existing_jobs，Runner 应真的执行它。"""
        from bzp_workbench.services.ai.fake_driver import FakeLLMDriver
        from bzp_workbench.services.ai.runner import run_turn

        driver = FakeLLMDriver()
        driver.text_delay = 0
        driver.reasoning_delay = 0

        called: list[dict] = []

        async def fake_list_jobs(*args, **kwargs):
            called.append(kwargs)
            return [], 0

        monkeypatch.setattr(
            "bzp_workbench.services.ai.tools.search_jobs.repo.list_jobs", fake_list_jobs
        )

        result = TurnResult()
        frames = await collect_frames(
            run_turn(
                driver,
                messages=[Message.system("sys"), Message.user("帮我在深圳找 Python 岗位")],
                mode="job_hunting",
                result=result,
                tool_context=ToolContext(db=_FakeDb(), conversation_id=1, model_config_id=1),
                model_label="假模型",
                model_capabilities=["text", "tool_calls"],
            )
        )

        names = [name for name, _ in frames]
        assert "tool_call" in names and "tool_result" in names
        assert len(called) == 1
        assert called[0]["city"] == "深圳"
        assert result.tool_results[0]["payload_type"] == "job_cards"
