"""薪资档位映射。

为什么需要它
------------
BOSS 直聘的薪资筛选**不是自由区间**，而是 6 个粗档位：

    402=3K以下  403=3-5K  404=5-10K  405=10-20K  406=20-50K  407=50K+

用户说「我要 15-25K」时，无法精确表达 —— 只能落到某个档位里。这带来一个
必须如实告知的问题：**筛选结果会比用户期望的宽**。例如选 406（20-50K）
会带出 45K 的岗位，远超用户的 25K 上限。

因此这里的策略是：
1. 把用户的区间映射到**覆盖其下限**的档位（宁可稍宽，不要漏掉用户想要的）
2. 把档位差异作为**明确提示**交给用户，绝不假装精确匹配

不做精确的区间过滤是刻意的：抓取阶段少拿数据，后面就得重抓一次，
代价远高于"多几条需要自己筛掉"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 档位码 → 人类可读标签（与爬虫 SALARY_MAP 保持一致）
SALARY_BUCKETS: dict[str, str] = {
    "0": "不限",
    "402": "3K以下",
    "403": "3-5K",
    "404": "5-10K",
    "405": "10-20K",
    "406": "20-50K",
    "407": "50K以上",
}

#: 档位码 → 数值区间（下限, 上限）；上限 None 表示无上限
_BUCKET_RANGES: dict[str, tuple[int, int | None]] = {
    "402": (0, 3),
    "403": (3, 5),
    "404": (5, 10),
    "405": (10, 20),
    "406": (20, 50),
    "407": (50, None),
}

#: 匹配 "15-25K" / "15k-25k" / "15~25" / "20K以上" / "20k以下" 等写法
_RANGE_PATTERN = re.compile(
    r"(?P<low>\d+(?:\.\d+)?)\s*(?:k|K|千)?\s*(?:-|~|—|至|到)\s*(?P<high>\d+(?:\.\d+)?)\s*(?:k|K|千)?"
)
_ABOVE_PATTERN = re.compile(r"(?P<low>\d+(?:\.\d+)?)\s*(?:k|K|千)?\s*(?:以上|\+|往上)")
_BELOW_PATTERN = re.compile(r"(?P<high>\d+(?:\.\d+)?)\s*(?:k|K|千)?\s*(?:以下|以内|以下)")


@dataclass
class SalaryResolution:
    """薪资解析结果。"""

    #: 爬虫用的档位码（"0" 表示不限）
    code: str
    #: 档位标签，如 "20-50K"
    label: str
    #: 用户原始输入
    raw: str
    #: 用户期望的区间（可能为 None）
    wanted: tuple[float, float | None] | None = None
    #: 被"撑宽"的说明；None 表示档位与期望基本一致
    note: str | None = None

    @property
    def is_unlimited(self) -> bool:
        return self.code == "0"

    def to_model_text(self) -> str:
        if self.is_unlimited:
            return "薪资：不限（用户未提出薪资要求）"
        text = f"薪资筛选档位：{self.label}（代码 {self.code}）"
        if self.note:
            text += f"\n注意：{self.note}"
        return text

    def to_payload(self) -> dict[str, object]:
        return {
            "code": self.code,
            "label": self.label,
            "raw": self.raw,
            "wanted": list(self.wanted) if self.wanted else None,
            "note": self.note,
        }


def _pick_bucket(low: float, high: float | None) -> str:
    """选出覆盖 ``low`` 的档位。

    刻意按**下限**落档而不是按上限或中位数：
    用户写「15-25K」时，他最不想要的是"低于 15K 的机会"被漏掉。
    按下限落档（→ 10-20K）能保证覆盖区间起点；偏宽的部分交给提示说明。
    """
    for code, (bucket_low, bucket_high) in _BUCKET_RANGES.items():
        if bucket_high is None:
            if low >= bucket_low:
                return code
            continue
        if bucket_low <= low < bucket_high:
            return code
    # 下限超出所有档位（例如输入 200），落到最高档
    return "407"


def _describe_gap(
    code: str, wanted: tuple[float, float | None]
) -> str | None:
    """档位与用户期望不一致时，生成一句诚实的说明。"""
    low, high = wanted
    bucket_low, bucket_high = _BUCKET_RANGES[code]

    parts: list[str] = []
    if bucket_low < low:
        parts.append(f"会包含低于 {_fmt(low)}K 的岗位")
    if bucket_high is None:
        if high is not None:
            parts.append(f"不设上限（会包含高于 {_fmt(high)}K 的岗位）")
    elif high is not None and bucket_high > high:
        parts.append(f"会包含高于 {_fmt(high)}K 的岗位，最高到 {bucket_high}K")

    if not parts:
        return None
    return (
        f"BOSS 直聘的薪资筛选只有粗档位（3K以下 / 3-5K / 5-10K / 10-20K / "
        f"20-50K / 50K以上），没有精确区间。已按你给的下限 {_fmt(low)}K "
        f"选择「{SALARY_BUCKETS[code]}」档，因此{'、'.join(parts)}。"
        "抓取回来的结果里可以再按具体薪资自己筛。"
    )


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def resolve_salary(raw: str | None) -> SalaryResolution:
    """把用户写的薪资要求解析成档位码。

    解析不出来时返回"不限"而不是报错 —— 薪资是选填项，
    因为一个格式问题就让整个抓取任务失败是不合理的。

    doctest 风格示例::

        resolve_salary("15-25K").code  -> "405"   # 按 15 落档
        resolve_salary("20-50K").code  -> "406"
        resolve_salary("50K以上").code  -> "407"
        resolve_salary(None).code      -> "0"
    """
    text = (raw or "").strip()
    if not text:
        return SalaryResolution(code="0", label=SALARY_BUCKETS["0"], raw="")

    match = _RANGE_PATTERN.search(text)
    if match:
        low = float(match.group("low"))
        high = float(match.group("high"))
        code = _pick_bucket(low, high)
        return SalaryResolution(
            code=code,
            label=SALARY_BUCKETS[code],
            raw=text,
            wanted=(low, high),
            note=_describe_gap(code, (low, high)),
        )

    match = _ABOVE_PATTERN.search(text)
    if match:
        low = float(match.group("low"))
        code = _pick_bucket(low, None)
        return SalaryResolution(
            code=code,
            label=SALARY_BUCKETS[code],
            raw=text,
            wanted=(low, None),
            note=_describe_gap(code, (low, None)),
        )

    match = _BELOW_PATTERN.search(text)
    if match:
        high = float(match.group("high"))
        # 只要上限时按下限 0 落档，再单独切到覆盖该上限的档位
        code = _pick_bucket(0, high)
        for candidate, (bucket_low, bucket_high) in _BUCKET_RANGES.items():
            if bucket_high is not None and high <= bucket_high:
                code = candidate
                break
        return SalaryResolution(
            code=code,
            label=SALARY_BUCKETS[code],
            raw=text,
            wanted=(0, high),
            note=_describe_gap(code, (0, high)),
        )

    # 只写了一个数字，如 "20K" —— 当作下限处理
    single = re.search(r"(?P<low>\d+(?:\.\d+)?)\s*(?:k|K|千)?", text)
    if single:
        low = float(single.group("low"))
        code = _pick_bucket(low, None)
        return SalaryResolution(
            code=code,
            label=SALARY_BUCKETS[code],
            raw=text,
            wanted=(low, None),
            note=_describe_gap(code, (low, None)),
        )

    return SalaryResolution(
        code="0",
        label=SALARY_BUCKETS["0"],
        raw=text,
        note=f"没能从「{text}」里解析出薪资区间，已按「不限」处理。",
    )


def label_for(code: str | None) -> str:
    """档位码 → 标签（未知码原样返回）。"""
    if not code:
        return SALARY_BUCKETS["0"]
    return SALARY_BUCKETS.get(str(code), str(code))


__all__ = [
    "SALARY_BUCKETS",
    "SalaryResolution",
    "label_for",
    "resolve_salary",
]
