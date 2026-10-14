"""工具：简历 × 岗位匹配度诊断 + STAR 面试题（模式二核心）。

为什么这个工具要**自己在内部调一次模型**
----------------------------------------
普通工具只做数据读写，但这个工具的产出是**结构化的诊断报告**：四个维度的
结论、风险点、5-8 道带 STAR 结构的面试题。如果让主模型用自由文本输出，
前端就没法稳定渲染（每轮格式都不一样，用户也没法逐题准备）。

所以这里用一次**带 JSON 约束的独立模型调用**产出结构化结果，
主模型拿到之后再用人话总结。

简历从哪来
----------
按优先级找，找不到就明确告诉模型去问用户，**不要猜**：

1. 本次消息附带的文档附件（最常见：用户新上传了简历）
2. 会话历史里最近一份**已抽取文本**的简历附件
   （用户上一轮传过，这轮只说"帮我分析"）
3. 用户把简历文本直接粘在对话里 → 由主模型自己处理，不需要本工具

注意：图片简历走多模态，其内容不在 ``extracted_text`` 里，
所以本工具**不处理图片简历**，会明确提示改用文档或粘贴文本。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from sqlalchemy import select

from ....models import ChatAttachment, ChatMessage, JobRecord
from ... import repository as repo
from ...llm import LLMError, Message
from ...logging_service import logger
from ..prompts import MATCH_REPORT_PROMPT
from .base import Tool, ToolContext, ToolError, ToolResult

#: 送进模型的简历文本上限。简历一般 2000-6000 字，留够余量。
MAX_RESUME_CHARS = 12_000

#: JD 文本上限
MAX_JD_CHARS = 6_000

#: 期望的题目数量区间（prompt 里也写了，这里用于校验与兜底提示）
MIN_QUESTIONS = 5
MAX_QUESTIONS = 8

#: 生成报告用的 max_tokens。
#:
#: ⚠️ 实测值不能小：一份完整的报告包含 4 个维度 + 若干风险点 +
#: 5-8 道带完整 STAR（S/T/A/R 四段）和 tips 的面试题，很容易超过 6000 token。
#: 第一版给了 4096，结果是 ``finish_reason=length``、JSON 被硬截断、
#: 整份报告解析失败 —— 用户看到的是"格式异常"而不是报告。
GENERATE_MAX_TOKENS = 8192

#: 四个诊断维度，顺序固定
DIMENSIONS = ("技能匹配", "经验匹配", "行业匹配", "风险点")

#: 合法 level 取值
_LEVELS = ("match", "partial", "mismatch")

#: 重试时给模型的最简结构提示（不重复完整 prompt，避免又把它带偏）
_SCHEMA_HINT = """{
  "verdict": "一句话结论",
  "overall_score": 0-100 的整数,
  "dimensions": [
    {"name": "技能匹配", "level": "match|partial|mismatch", "summary": "", "evidence": [], "gaps": []},
    {"name": "经验匹配", "level": "match|partial|mismatch", "summary": "", "evidence": [], "gaps": []},
    {"name": "行业匹配", "level": "match|partial|mismatch", "summary": "", "evidence": [], "gaps": []},
    {"name": "风险点",   "level": "match|partial|mismatch", "summary": "", "evidence": [], "gaps": []}
  ],
  "strengths": ["..."],
  "risks": [{"risk": "", "why": "", "how_to_handle": ""}],
  "questions": [
    {"question": "", "category": "技术|项目|行为|岗位认知", "focus": "",
     "star": {"situation": "", "task": "", "action": "", "result": ""}, "tips": ""}
  ],
  "actions": ["..."]
}
questions 必须有 5 到 8 道。"""


class AnalyzeMatchTool(Tool):
    name = "analyze_match"
    description = (
        "对比用户简历与目标岗位 JD，输出《匹配度诊断报告》与 5-8 道 STAR 面试题。"
        "用户要求分析简历匹配度、准备面试时使用。"
        "简历需已作为文档附件上传（或粘贴在对话里）。"
        "必须提供目标岗位：用 local_id / job_id 指定本地岗位，或直接给 jd_text。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "local_id": {
                "type": "integer",
                "description": "目标岗位在本地库的编号（search_existing_jobs 结果里的 id）",
            },
            "job_id": {
                "type": "string",
                "description": "BOSS 直聘的岗位编号",
            },
            "jd_text": {
                "type": "string",
                "description": "直接粘贴的目标岗位 JD 文本（用户没有指定具体岗位时用）",
            },
            "resume_text": {
                "type": "string",
                "description": (
                    "简历文本。仅当用户把简历直接粘贴在对话里、且你想显式传入时使用；"
                    "通常留空，工具会自动读取已上传的简历附件"
                ),
            },
            "focus": {
                "type": "string",
                "description": "用户特别关注的方向，如「重点看技术深度」「我担心跳槽频繁」",
            },
        },
        "required": [],
    }
    #: 在面试辅导与找岗位两种模式下都暴露。
    #:
    #: 为什么要包含 job_hunting：用户说「这个岗位适合我吗」「帮我看看我够不够格」
    #: 时，路由很可能判成 job_hunting（因为提到了"岗位"）。如果这个工具只在
    #: 面试辅导模式下可见，模型在那个模式里就退化成凭摘要硬编分析 —— 正是我们
    #: 想避免的。它也只是读数据 + 调一次模型，没有副作用，放开是安全的。
    modes = frozenset({"interview_coach", "job_hunting"})

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        started = time.monotonic()

        if context.adapter is None:
            raise ToolError(
                "当前环境无法生成诊断报告（缺少模型适配器）",
                hint="请重试；若持续失败请查看后端日志。",
            )

        # ---- 1. 目标岗位 ----
        job, jd_text, jd_source = await self._resolve_jd(arguments, context)
        if not jd_text.strip():
            raise ToolError(
                "没有可用的目标岗位 JD",
                hint=(
                    "请先向用户确认目标岗位：可以是库里已有的岗位"
                    "（先用 search_existing_jobs 检索并取 local_id），"
                    "或者让用户把 JD 文本粘贴过来。不要自己编一份 JD。"
                ),
            )

        # ---- 2. 简历 ----
        resume_text, resume_source = await self._resolve_resume(arguments, context)
        if not resume_text.strip():
            raise ToolError(
                "没有找到可解析的简历内容",
                hint=(
                    "请让用户上传简历（PDF / Word 文档），或把简历文本直接粘贴到对话里。"
                    "注意：**图片格式的简历无法用于本工具**，"
                    "因为图片内容不参与文本抽取。"
                ),
            )

        # ---- 3. 调用模型产出结构化报告 ----
        report = await self._generate_report(
            context, resume_text, jd_text, arguments
        )

        payload = {
            "report": report,
            "job": self._job_brief(job, jd_text),
            "resume_source": resume_source,
            "jd_source": jd_source,
            "question_count": len(report.get("questions") or []),
        }

        return ToolResult(
            payload_type="match_report",
            data=payload,
            model_text=self._model_text(report, resume_source, jd_source),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    # ------------------------------------------------------------------
    # 目标岗位
    # ------------------------------------------------------------------
    async def _resolve_jd(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> tuple[JobRecord | None, str, str]:
        """返回 (岗位记录或 None, JD 文本, 来源说明)。"""
        pasted = self.get_str(arguments, "jd_text")
        if pasted:
            return None, pasted[:MAX_JD_CHARS], "用户粘贴的 JD 文本"

        local_id = self.get_int(arguments, "local_id")
        biz_id = self.get_str(arguments, "job_id")

        job: JobRecord | None = None
        if local_id is not None:
            job = await repo.get_job(context.db, local_id)
        elif biz_id:
            job = await repo.get_job_by_biz_id(context.db, biz_id)

        if job is None:
            return None, "", ""

        parts = [
            f"岗位：{job.title}",
            f"公司：{job.company}",
            f"薪资：{job.salary or '面议'}",
            f"地点：{job.location or job.city or '未知'}",
            f"经验要求：{job.experience or '不限'}　学历要求：{job.degree or '不限'}",
        ]
        if job.welfare:
            parts.append(f"福利：{job.welfare}")
        for label, value in (
            ("岗位职责", job.job_duty),
            ("任职要求", job.job_requirement),
            ("JD", job.jd),
        ):
            if value:
                parts.append(f"\n【{label}】\n{value}")

        text = "\n".join(parts)
        return job, text[:MAX_JD_CHARS], f"本地岗位 #{job.id}（{job.title}）"

    # ------------------------------------------------------------------
    # 简历
    # ------------------------------------------------------------------
    async def _resolve_resume(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> tuple[str, str]:
        """返回 (简历文本, 来源说明)。

        优先用显式传入的文本，其次找会话里的文档附件（本轮优先，再往前找）。
        """
        explicit = self.get_str(arguments, "resume_text")
        if explicit:
            return explicit[:MAX_RESUME_CHARS], "调用方传入的简历文本"

        # 本轮消息的附件优先（用户刚上传的），没有就找历史上最近的一份
        attachments = await self._attachments_with_text(context)
        if not attachments:
            return "", ""

        newest = attachments[0]
        text = (newest.extracted_text or "")[:MAX_RESUME_CHARS]
        if not text:
            return "", ""
        return text, f"附件「{newest.filename}」"

    async def _attachments_with_text(
        self, context: ToolContext
    ) -> list[ChatAttachment]:
        """取会话里有抽取文本的文档附件，按"最近消息优先"排序。

        为什么优先本轮：用户可能换了一份简历再问，用旧的会给出错误诊断。
        """
        rows = (
            (
                await context.db.execute(
                    select(ChatAttachment)
                    .where(
                        ChatAttachment.conversation_id == context.conversation_id,
                        ChatAttachment.extracted_text.is_not(None),
                        ChatAttachment.kind != "image",
                    )
                    .order_by(ChatAttachment.id.desc())
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return []

        # 找出本轮用户消息引用的附件 id，排到最前面
        last_user_message = (
            await context.db.execute(
                select(ChatMessage)
                .where(
                    ChatMessage.conversation_id == context.conversation_id,
                    ChatMessage.role == "user",
                )
                .order_by(ChatMessage.id.desc())
                .limit(1)
            )
        ).scalars().first()

        current_ids: set[int] = set()
        if last_user_message and last_user_message.attachments_json:
            try:
                parsed = json.loads(last_user_message.attachments_json)
                if isinstance(parsed, list):
                    current_ids = {int(item) for item in parsed}
            except (ValueError, TypeError):
                current_ids = set()

        current = [row for row in rows if row.id in current_ids]
        history = [row for row in rows if row.id not in current_ids]
        return current + history

    # ------------------------------------------------------------------
    # 模型调用
    # ------------------------------------------------------------------
    async def _generate_report(
        self,
        context: ToolContext,
        resume_text: str,
        jd_text: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """调用模型产出结构化报告。"""
        focus = self.get_str(arguments, "focus")
        user_parts = [
            "【简历内容】",
            resume_text,
            "",
            "【目标岗位 JD】",
            jd_text,
        ]
        if focus:
            user_parts += ["", f"【用户特别关注】{focus}"]

        try:
            response = await context.adapter.chat_completion(
                [
                    Message.system(MATCH_REPORT_PROMPT),
                    Message.user("\n".join(user_parts)),
                ],
                temperature=0.2,
                max_tokens=GENERATE_MAX_TOKENS,
            )
        except LLMError as exc:
            raise ToolError(
                f"生成诊断报告失败：{exc.message}",
                hint=exc.suggestion or "请稍后重试。",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise ToolError(
                f"生成诊断报告时出错：{type(exc).__name__}: {exc}",
                hint="请稍后重试；若持续失败请查看后端日志。",
            ) from exc

        raw = response.text or ""
        report = parse_report(raw)

        # 第一次没解析出来时**重试一次**，用更严厉的 JSON 约束。
        #
        # 为什么值得多花一次调用：报告解析失败意味着用户拿到的是一整块原始
        # 文本，前端卡片是空的 —— 这对"模式二"来说是**功能不可用**，不是
        # 小瑕疵。实测模型第一次输出时确实会带 markdown 标题或解释性前言。
        if report is None:
            logger.info("诊断报告首次解析失败，重试一次（输出长度=%s）", len(raw))
            retry = await self._retry_for_json(context, user_parts)
            if retry is not None:
                report = parse_report(retry)
            if report is None:
                logger.warning(
                    "诊断报告两次解析均失败，降级为纯文本（首次输出前 200 字：%s）",
                    raw[:200].replace("\n", " "),
                )

        if report is None:
            # 彻底失败：降级成纯文本，而不是让整个工具失败 ——
            # 用户至少还能看到诊断内容，只是界面不渲染成卡片。
            #
            # ⚠️ overall_score 用 None 而不是 0：0 是一个**真实可能的评分**
            # （完全不匹配），显示出来会让用户以为模型真的给了 0 分。
            # 实测主模型不得不主动纠正这一点（"卡片的 0 分是解析失败的占位值"），
            # 说明这个占位值本身就在误导人。
            return {
                "parse_error": True,
                "raw_text": raw[:MAX_RESUME_CHARS],
                "verdict": "报告格式异常，未能生成结构化诊断",
                "overall_score": None,
                "dimensions": [],
                "strengths": [],
                "risks": [],
                "questions": [],
                "actions": [],
            }
        return report

    async def _retry_for_json(
        self, context: ToolContext, user_parts: list[str]
    ) -> str | None:
        """重试一次，用最简短的指令逼模型只输出 JSON。"""
        try:
            response = await context.adapter.chat_completion(
                [
                    Message.system(
                        "你是一个只输出 JSON 的接口。"
                        "严格按照用户给出的结构输出一个 JSON 对象，"
                        "不要 markdown 代码块、不要标题、不要任何解释文字。"
                        "第一个字符必须是 {，最后一个字符必须是 }。"
                    ),
                    Message.user(
                        "\n".join(user_parts)
                        + "\n\n请重新输出完整 JSON。结构要求（必须包含这些字段）：\n"
                        + _SCHEMA_HINT
                    ),
                ],
                temperature=0.0,
                max_tokens=GENERATE_MAX_TOKENS,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("诊断报告重试失败：%s", exc)
            return None
        return response.text or ""

    # ------------------------------------------------------------------
    # 输出
    # ------------------------------------------------------------------
    @staticmethod
    def _job_brief(job: JobRecord | None, jd_text: str) -> dict[str, Any] | None:
        if job is None:
            return None
        return {
            "id": job.id,
            "title": job.title,
            "company": job.company,
            "salary": job.salary or "面议",
            "city": job.city,
            "job_link": job.job_link,
        }

    @staticmethod
    def _model_text(
        report: dict[str, Any], resume_source: str, jd_source: str
    ) -> str:
        """给主模型看的摘要。

        刻意**不把完整报告塞进去**：报告已经作为结构化载荷存下来了，
        前端会渲染成卡片。主模型只需要知道"报告已生成、结论是什么"，
        从而用几句话做导读，而不是复述整份报告。
        """
        score = report.get("overall_score")
        questions = report.get("questions") or []
        risks = report.get("risks") or []
        actions = report.get("actions") or []

        lines = [
            "《匹配度诊断报告》已生成。",
            f"简历来源：{resume_source}",
            f"目标岗位：{jd_source}",
            f"综合匹配度：{score}/100",
            f"结论：{report.get('verdict') or '（无）'}",
            f"共 {len(questions)} 道面试题、{len(risks)} 个风险点、{len(actions)} 条建议。",
            "",
            "界面已经把完整报告渲染成卡片（四个维度、风险点、STAR 面试题）。",
            "**你只需要用几句话做导读**：",
            "- 点出最关键的 1-2 个落差或亮点",
            "- 提醒用户风险点里最需要注意的那个",
            "- 建议他先看哪一部分",
            "**不要逐条复述报告内容** —— 那会和卡片重复，而且很长。",
        ]

        if report.get("parse_error"):
            lines.append(
                "\n⚠️ 注意：报告的结构化解析失败了，卡片可能显示异常。"
                "请把模型原始输出里的关键结论用自己的话讲给用户，"
                "并说明格式异常。"
            )
        return "\n".join(lines)


# ======================================================================
# 报告解析
# ======================================================================
def parse_report(raw: str) -> dict[str, Any] | None:
    """把模型输出解析成规范化报告；失败返回 None。

    容错处理（模型经常不守规矩）：
    * markdown 代码块包裹
    * 前后夹带解释文字
    * **输出被 max_tokens 截断**（实测最常见的一种，见 :func:`_repair_truncated`）
    * 字段缺失 / 类型不对
    * level 用了中文或其它写法
    """
    text = (raw or "").strip()
    if not text:
        return None

    # 去掉 ```json 围栏
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()

    payload: Any = None
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        # 从文本里抠出最外层的 {...}
        start = text.find("{")
        if start != -1:
            # ⚠️ 不能只靠 rfind("}")：被截断的输出**可能一个 } 都没有**
            # （模型还在写第一个维度就被切断了）。这时 rfind 返回 -1，
            # 下面的修复分支就永远走不到 —— 而修复恰恰是为这种情况准备的。
            end = text.rfind("}")
            candidate = text[start : end + 1] if end > start else text[start:]
            try:
                payload = json.loads(candidate)
            except (ValueError, TypeError):
                # 再试一次：按截断修复。这一步很关键 ——
                # 实测报告经常因为 max_tokens 被切断，而前面大部分内容
                # 完全可用（维度、部分面试题都在）。直接放弃太浪费。
                payload = _repair_truncated(candidate)

    if not isinstance(payload, dict):
        return None

    # 完全没救回来的情况：只剩一两个无意义字段（甚至像 "{" 这样解析出空对象）。
    # 这种"报告"必须返回 None，让调用方降级为纯文本 —— 否则界面会渲染出一张
    # 全是占位值（0 分、四个"报告未覆盖该维度"）的卡片，比看到原始文本更糟：
    # 用户会以为模型真的给了 0 分。
    if not _has_meaningful_content(payload):
        return None

    return _normalize(payload)


def _has_meaningful_content(payload: dict[str, Any]) -> bool:
    """判断解析结果里是否有真实内容（而不是空壳）。"""
    if str(payload.get("verdict") or "").strip():
        return True
    questions = payload.get("questions")
    if isinstance(questions, list) and questions:
        return True
    for key in ("dimensions", "strengths", "risks", "actions"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            return True
    # 只有分数没有结论也不够 —— 分数单独存在说明输出基本没写出来
    return False


def _repair_truncated(text: str) -> dict[str, Any] | None:
    """尝试修复被截断的 JSON。

    思路：**回退到最后一个完整的逗号分隔点**，丢掉后面那个（可能只写了一半的）
    元素，再补齐缺失的闭合符号。

    为什么不"尽量保留残缺的值"：一个被截断的字符串或数字，补上引号也往往
    不再是原意（例如 ``"summary": "JD 要求 Java，简历全是`` 后面本来还有内容）。
    直接丢掉这个元素，保住前面所有**完整**的维度/风险/题目，结果更诚实也更好用。

    实测效果：因为 max_tokens 被切断的报告，能救回四个维度、全部风险点
    和前面几道 STAR 完整的面试题 —— 远好过"格式异常，什么都没有"。
    """
    if not text.startswith("{"):
        return None

    # ---- 找出所有"字符串外"的逗号位置 ----
    string_commas: list[int] = []
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == ",":
            string_commas.append(index)

    # 从最后一个逗号往前试：截到该逗号之前，补全括号，看能否解析
    for cut in reversed(string_commas):
        candidate = _balance(text[:cut])
        if candidate is None:
            continue
        try:
            payload = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(payload, dict):
            return payload

    # 没有逗号可分（例如只有开头几个字段）时，最后一个办法：
    # 直接从末尾补括号试试
    candidate = _balance(text)
    if candidate is None:
        return None
    try:
        payload = json.loads(candidate)
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _balance(text: str) -> str | None:
    """给可能未闭合的 JSON 片段补上引号与括号。

    从头扫描（跳过字符串内部），得到未闭合的括号栈，倒序补齐。
    """
    if not text.startswith("{"):
        return None

    stack: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]" and stack:
            stack.pop()

    if not stack:
        # 括号本来是平衡的，但整体仍解析失败 → 不是截断问题
        return None

    candidate = text
    if in_string:
        # 字符串被截断：把未闭合的引号关上。这时的内容是残缺的，
        # 但由于我们总是从逗号处切开，这种情况很少同时发生。
        candidate += '"'
    for opener in reversed(stack):
        candidate += "}" if opener == "{" else "]"
    return candidate


def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
    """补齐缺失字段、修正类型、限制取值范围。

    这个函数的作用是**保证前端渲染不会崩** —— 模型少给一个字段或类型写错，
    不应该让整张卡片白屏。
    """

    def as_list(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            return [value] if value.strip() else []
        return [value]

    def as_str(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return str(value)

    # ---- 总分 ----
    score: int = 0
    raw_score = payload.get("overall_score", payload.get("score"))
    try:
        score = int(float(raw_score))  # 模型有时给 "75" 或 75.0
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(score, 100))

    # ---- 维度 ----
    dimensions: list[dict[str, Any]] = []
    by_name = {
        as_str(item.get("name")): item
        for item in as_list(payload.get("dimensions"))
        if isinstance(item, dict)
    }
    for name in DIMENSIONS:
        item = by_name.get(name)
        if item is None:
            dimensions.append(
                {
                    "name": name,
                    "level": "partial",
                    "summary": "报告未覆盖该维度",
                    "evidence": [],
                    "gaps": [],
                }
            )
            continue
        level = as_str(item.get("level")).lower()
        if level not in _LEVELS:
            # 模型偶尔写中文或别的词，做一次映射
            mapping = {
                "匹配": "match",
                "部分匹配": "partial",
                "不匹配": "mismatch",
                "match": "match",
                "partial": "partial",
                "mismatch": "mismatch",
            }
            level = mapping.get(as_str(item.get("level")), "partial")
        dimensions.append(
            {
                "name": name,
                "level": level,
                "summary": as_str(item.get("summary")),
                "evidence": [as_str(x) for x in as_list(item.get("evidence")) if as_str(x)],
                "gaps": [as_str(x) for x in as_list(item.get("gaps")) if as_str(x)],
            }
        )

    # ---- 风险点 ----
    risks: list[dict[str, str]] = []
    for item in as_list(payload.get("risks")):
        if isinstance(item, dict):
            risks.append(
                {
                    "risk": as_str(item.get("risk")),
                    "why": as_str(item.get("why")),
                    "how_to_handle": as_str(
                        item.get("how_to_handle") or item.get("handle")
                    ),
                }
            )
        elif isinstance(item, str) and item.strip():
            risks.append({"risk": item.strip(), "why": "", "how_to_handle": ""})

    # ---- 面试题 ----
    questions: list[dict[str, Any]] = []
    for item in as_list(payload.get("questions"))[:_MAX_QUESTIONS_SLICE]:
        if not isinstance(item, dict):
            if isinstance(item, str) and item.strip():
                questions.append(
                    {
                        "question": item.strip(),
                        "category": "",
                        "focus": "",
                        "star": {"situation": "", "task": "", "action": "", "result": ""},
                        "tips": "",
                    }
                )
            continue
        star_raw = item.get("star")
        star = star_raw if isinstance(star_raw, dict) else {}
        questions.append(
            {
                "question": as_str(item.get("question")),
                "category": as_str(item.get("category")),
                "focus": as_str(item.get("focus")),
                "star": {
                    "situation": as_str(star.get("situation")),
                    "task": as_str(star.get("task")),
                    "action": as_str(star.get("action")),
                    "result": as_str(star.get("result")),
                },
                "tips": as_str(item.get("tips")),
            }
        )
    questions = [item for item in questions if item["question"]]

    return {
        "verdict": as_str(payload.get("verdict")),
        "overall_score": score,
        "dimensions": dimensions,
        "strengths": [as_str(x) for x in as_list(payload.get("strengths")) if as_str(x)],
        "risks": risks,
        "questions": questions,
        "actions": [as_str(x) for x in as_list(payload.get("actions")) if as_str(x)],
        "parse_error": False,
    }


#: 解析时最多保留的题目数（比 MAX_QUESTIONS 宽松一点，避免把模型多给的直接丢掉）
_MAX_QUESTIONS_SLICE = 12


__all__ = [
    "AnalyzeMatchTool",
    "DIMENSIONS",
    "GENERATE_MAX_TOKENS",
    "MAX_QUESTIONS",
    "MAX_RESUME_CHARS",
    "MIN_QUESTIONS",
    "parse_report",
]
