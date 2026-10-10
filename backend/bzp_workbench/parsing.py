"""薪资与 JD 文本解析。

爬虫给出的 ``salary`` 是 BOSS 的明文薪资串，形如：

* ``20-40K``
* ``20-40K·13薪``
* ``15-25K·14薪``
* ``300-500元/天``
* ``8-12万``
* ``面议``

这里把它归一化成「月薪千元（K）」区间，供图表统计和薪资筛选使用。
同时负责把 JD 全文拆成「岗位职责」和「岗位要求」两部分。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 中文数字单位
_UNIT_K = r"[kK千]"
_UNIT_W = r"[wW万]"

# 例如 20-40K / 20-40K·13薪 / 1.5-2万
_RANGE_RE = re.compile(
    r"(?P<low>\d+(?:\.\d+)?)\s*[-~－—到至]\s*(?P<high>\d+(?:\.\d+)?)\s*(?P<unit>[kK千wW万]?)"
)
# 例如 20K以上 / 20K+
_ABOVE_RE = re.compile(r"(?P<low>\d+(?:\.\d+)?)\s*(?P<unit>[kK千wW万]?)\s*(?:以上|\+)")
# 例如 20K以下
_BELOW_RE = re.compile(r"(?P<high>\d+(?:\.\d+)?)\s*(?P<unit>[kK千wW万]?)\s*以下")
# 单个数字 例如 20K
_SINGLE_RE = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>[kK千wW万])")

# 日薪/时薪，不参与月薪统计
_DAILY_RE = re.compile(r"(?:元|块)?\s*/\s*(?:天|日|小时|时)")
_MONTHLY_COUNT_RE = re.compile(r"[·*xX×]\s*(\d{2})\s*薪")


@dataclass(frozen=True)
class SalaryRange:
    """归一化后的薪资区间，单位统一为「千元/月」。"""

    low: int | None = None
    high: int | None = None
    avg: int | None = None
    months: int = 12
    is_daily: bool = False
    negotiable: bool = False

    @property
    def has_value(self) -> bool:
        return self.low is not None or self.high is not None


def _to_k(value: float, unit: str) -> float:
    """把带单位的数字折算到「千元」。"""
    if unit in ("w", "W", "万"):
        return value * 10.0
    return value


def parse_salary(text: str | None) -> SalaryRange:
    """解析薪资字符串。无法识别时返回空区间（不瞎猜）。"""
    if not text:
        return SalaryRange()

    raw = str(text).strip()
    if not raw:
        return SalaryRange()
    if "面议" in raw or "薪资面议" in raw:
        return SalaryRange(negotiable=True)

    months = 12
    month_match = _MONTHLY_COUNT_RE.search(raw)
    if month_match:
        try:
            months = int(month_match.group(1))
        except ValueError:
            months = 12

    is_daily = bool(_DAILY_RE.search(raw))
    if is_daily:
        # 日薪/时薪口径不同，不折算成月薪，只标记
        return SalaryRange(is_daily=True, months=months)

    match = _RANGE_RE.search(raw)
    if match:
        low = _to_k(float(match.group("low")), match.group("unit"))
        high = _to_k(float(match.group("high")), match.group("unit"))
        if low > high:
            low, high = high, low
        return SalaryRange(
            low=int(round(low)), high=int(round(high)), avg=int(round((low + high) / 2)), months=months
        )

    match = _ABOVE_RE.search(raw)
    if match:
        low = _to_k(float(match.group("low")), match.group("unit"))
        return SalaryRange(low=int(round(low)), high=None, avg=None, months=months)

    match = _BELOW_RE.search(raw)
    if match:
        high = _to_k(float(match.group("high")), match.group("unit"))
        return SalaryRange(low=None, high=int(round(high)), avg=None, months=months)

    match = _SINGLE_RE.search(raw)
    if match:
        value = int(round(_to_k(float(match.group("value")), match.group("unit"))))
        return SalaryRange(low=value, high=value, avg=value, months=months)

    return SalaryRange(months=months)


def salary_bucket(avg: int | None) -> str:
    """把月薪均值落到图表桶里。"""
    if avg is None:
        return "薪资未知"
    if avg < 10:
        return "10K 以下"
    if avg < 15:
        return "10-15K"
    if avg < 20:
        return "15-20K"
    if avg < 30:
        return "20-30K"
    if avg < 50:
        return "30-50K"
    return "50K 以上"


SALARY_BUCKET_ORDER = [
    "10K 以下",
    "10-15K",
    "15-20K",
    "20-30K",
    "30-50K",
    "50K 以上",
    "薪资未知",
]

# 用户填写的薪资要求 -> 期望区间（千元/月）
_USER_SALARY_RE = re.compile(r"(\d+(?:\.\d+)?)")


def parse_user_salary_expectation(text: str | None) -> tuple[int | None, int | None]:
    """解析用户填写的薪资要求，如 ``15k-25k`` / ``15-25K`` / ``20K以上``。

    返回期望的 (下限, 上限)，单位千元/月；无法解析返回 (None, None)。
    用途是前端高亮「达到期望」的岗位，**不做任何计费或额度判断**。
    """
    if not text:
        return None, None

    cleaned = str(text).strip().lower().replace("k", "").replace("千", "")
    numbers = [float(item) for item in _USER_SALARY_RE.findall(cleaned)]
    if not numbers:
        return None, None
    if len(numbers) == 1:
        if "以上" in cleaned or "+" in cleaned:
            return int(numbers[0]), None
        if "以下" in cleaned:
            return None, int(numbers[0])
        return int(numbers[0]), int(numbers[0])
    low, high = sorted(numbers[:2])
    return int(low), int(high)


# ============================================================
# JD 全文拆分
# ============================================================
_DUTY_HEADINGS = ("岗位职责", "职位描述", "工作内容", "职位职责", "岗位描述", "工作职责", "你将负责")
_REQUIREMENT_HEADINGS = (
    "任职要求",
    "岗位要求",
    "任职资格",
    "职位要求",
    "岗位需求",
    "任职条件",
    "我们希望",
    "技能要求",
    "任职要求：",
)

_HEADING_RE = re.compile(r"^\s*(?:[【\[（(]?\s*)([^】\]）)]{2,12})(?:\s*[】\]）)]?)\s*[:：]?\s*$")


def split_jd(jd: str | None) -> tuple[str | None, str | None]:
    """把 JD 全文拆成（岗位职责, 岗位要求）。

    找不到明确小标题时返回 ``(None, None)``，由调用方决定是否全文展示，
    不强行切分导致内容错位。
    """
    if not jd:
        return None, None

    lines = str(jd).splitlines()
    marks: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or len(stripped) > 20:
            continue
        normalized = stripped.rstrip(":：").strip("【】[]（）() ")
        if any(key in normalized for key in _DUTY_HEADINGS) and len(normalized) <= 8:
            marks.append((index, "duty"))
        elif any(key in normalized for key in _REQUIREMENT_HEADINGS) and len(normalized) <= 8:
            marks.append((index, "requirement"))

    if not marks:
        return None, None

    duty_parts: list[str] = []
    requirement_parts: list[str] = []
    for position, (index, kind) in enumerate(marks):
        end = marks[position + 1][0] if position + 1 < len(marks) else len(lines)
        body = "\n".join(lines[index + 1 : end]).strip()
        if not body:
            continue
        if kind == "duty":
            duty_parts.append(body)
        else:
            requirement_parts.append(body)

    duty = "\n".join(duty_parts).strip() or None
    requirement = "\n".join(requirement_parts).strip() or None
    return duty, requirement


# ============================================================
# 技能关键词提取
# ============================================================
# 命中即视为技能词（保持小写比较）
SKILL_DICTIONARY: tuple[str, ...] = (
    # 语言 / 运行时
    "python", "java", "golang", "go语言", "c++", "c#", "javascript", "typescript",
    "php", "ruby", "rust", "kotlin", "swift", "scala", "lua", "dart", "objective-c",
    "shell", "bash",
    # 后端框架
    "fastapi", "django", "flask", "tornado", "sanic", "spring", "spring boot",
    "springcloud", "mybatis", "netty", "dubbo", "gin", "beego", "grpc", "nestjs",
    "express", "koa", "laravel", "thinkphp",
    # 前端
    "vue", "vue3", "vue2", "react", "angular", "svelte", "next.js", "nuxt",
    "element plus", "element ui", "ant design", "vant", "uni-app", "taro",
    "webpack", "vite", "rollup", "babel", "sass", "less", "tailwind",
    "html5", "css3", "javascript es6", "es6", "jquery", "echarts", "three.js",
    "微信小程序", "小程序", "h5",
    # 数据库 / 存储
    "mysql", "postgresql", "oracle", "sql server", "sqlite", "mongodb", "redis",
    "elasticsearch", "clickhouse", "hbase", "cassandra", "tidb", "oceanbase",
    "memcached", "rocketmq", "kafka", "rabbitmq", "pulsar", "nacos", "zookeeper",
    # 大数据 / AI
    "hadoop", "spark", "flink", "hive", "presto", "doris", "pandas", "numpy",
    "pytorch", "tensorflow", "scikit-learn", "xgboost", "lightgbm", "opencv",
    "transformer", "llm", "大模型", "rag", "langchain", "agent", "prompt",
    "机器学习", "深度学习", "自然语言处理", "nlp", "推荐算法", "计算机视觉",
    "数据挖掘", "数据分析", "数据仓库", "特征工程", "a/b测试",
    # 运维 / 云原生
    "docker", "kubernetes", "k8s", "devops", "ci/cd", "jenkins", "gitlab ci",
    "github actions", "ansible", "saltstack", "terraform", "prometheus", "grafana",
    "elk", "skywalking", "linux", "nginx", "tomcat", "aws", "aliyun", "阿里云",
    "腾讯云", "华为云", "azure", "微服务", "分布式", "高并发", "高可用", "中间件",
    # 移动端
    "android", "ios", "flutter", "react native", "harmonyos", "鸿蒙", "webview",
    # 工程实践
    "git", "svn", "junit", "pytest", "unittest", "selenium", "appium", "jmeter",
    "postman", "restful", "graphql", "websocket", "tcp/ip", "http", "oauth",
    "设计模式", "重构", "敏捷开发", "scrum", "代码review", "单元测试",
    # 产品 / 运营 / 设计
    "axure", "xmind", "visio", "墨刀", "需求分析", "数据分析能力", "项目管理",
    "产品设计", "用户研究", "原型设计", "prd", "sql", "excel", "power bi",
    "tableau", "photoshop", "illustrator", "figma", "sketch", "ui设计", "交互设计",
    "新媒体运营", "内容运营", "用户运营", "活动运营", "社群运营", "seo", "sem",
    # 通用软技能
    "沟通能力", "团队协作", "抗压能力", "学习能力", "英语读写", "英语口语",
    "责任心", "执行力", "逻辑思维",
)

# 这些词太泛，命中也不当作技能标签输出
_SKILL_STOPWORDS = frozenset({"go", "r", "c", "sql", "http", "git"})

# 预编译：字典里的词按长度倒序，保证「spring boot」优先于「spring」
_SKILL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (
        skill,
        re.compile(r"(?<![a-z0-9+#])" + re.escape(skill) + r"(?![a-z0-9+#])", re.IGNORECASE),
    )
    for skill in sorted(SKILL_DICTIONARY, key=len, reverse=True)
)


def extract_skill_keywords(
    text: str | None, limit: int = 20, extra: list[str] | None = None
) -> list[str]:
    """从 JD 文本中抽取高频技能关键词。

    ``extra`` 用于把爬虫已经给好的 ``skills`` 字段一并纳入（优先展示）。
    """
    results: list[str] = []
    seen: set[str] = set()

    # 爬虫直接给的结构化 skills 命中率最高，排在前面
    for item in extra or []:
        cleaned = str(item).strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            results.append(cleaned)

    if text:
        haystack = str(text)
        for skill, pattern in _SKILL_PATTERNS:
            lowered = skill.lower()
            if lowered in _SKILL_STOPWORDS or lowered in seen:
                continue
            if pattern.search(haystack):
                seen.add(lowered)
                results.append(_display_skill(skill))

    return results[:limit]


_DISPLAY_OVERRIDES = {
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "python": "Python",
    "java": "Java",
    "golang": "Golang",
    "vue": "Vue",
    "vue3": "Vue3",
    "vue2": "Vue2",
    "react": "React",
    "angular": "Angular",
    "mysql": "MySQL",
    "postgresql": "PostgreSQL",
    "redis": "Redis",
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "k8s": "K8s",
    "nginx": "Nginx",
    "linux": "Linux",
    "git": "Git",
    "sql": "SQL",
    "html5": "HTML5",
    "css3": "CSS3",
    "es6": "ES6",
    "fastapi": "FastAPI",
    "django": "Django",
    "flask": "Flask",
    "spring": "Spring",
    "spring boot": "Spring Boot",
    "springcloud": "Spring Cloud",
    "mybatis": "MyBatis",
    "kafka": "Kafka",
    "rocketmq": "RocketMQ",
    "elasticsearch": "Elasticsearch",
    "mongodb": "MongoDB",
    "clickhouse": "ClickHouse",
    "pytorch": "PyTorch",
    "tensorflow": "TensorFlow",
    "llm": "LLM",
    "rag": "RAG",
    "nlp": "NLP",
    "sql server": "SQL Server",
    "power bi": "Power BI",
    "ci/cd": "CI/CD",
    "tcp/ip": "TCP/IP",
    "aws": "AWS",
    "seo": "SEO",
    "sem": "SEM",
}


def _display_skill(skill: str) -> str:
    return _DISPLAY_OVERRIDES.get(skill.lower(), skill)


def normalize_experience(text: str | None) -> str | None:
    """把 ``经验`` 字段归一化，``不限`` 视为空。"""
    if not text:
        return None
    cleaned = str(text).strip()
    if not cleaned or cleaned in ("不限", "经验不限", "-"):
        return None
    return cleaned


def normalize_degree(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = str(text).strip()
    if not cleaned or cleaned in ("不限", "学历不限", "-"):
        return None
    return cleaned


def split_tags(tags: str | None) -> tuple[str | None, str | None]:
    """爬虫把「经验 | 学历」拼在 ``tags`` 里，这里拆成两个字段。

    形如 ``3-5年 | 本科``。
    """
    if not tags:
        return None, None
    parts = [item.strip() for item in str(tags).split("|") if item.strip()]
    if not parts:
        return None, None
    if len(parts) == 1:
        return normalize_experience(parts[0]), None
    return normalize_experience(parts[0]), normalize_degree(parts[1])
