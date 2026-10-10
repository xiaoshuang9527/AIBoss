"""单元测试：薪资解析、JD 拆分、技能词提取、工具函数。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bzp_workbench.parsing import (
    normalize_degree,
    normalize_experience,
    parse_salary,
    parse_user_salary_expectation,
    salary_bucket,
    split_jd,
    split_tags,
)
from bzp_workbench.utils import day_bounds_cst, fingerprint, read_json, to_naive_utc, write_json


class TestParseSalary:
    @pytest.mark.parametrize(
        ("text", "low", "high", "avg"),
        [
            ("20-40K", 20, 40, 30),
            ("15-25K·13薪", 15, 25, 20),
            ("8-12万", 80, 120, 100),
            ("1.5-2万", 15, 20, 18),
            ("30-50K·14薪", 30, 50, 40),
        ],
    )
    def test_ranges(self, text, low, high, avg):
        parsed = parse_salary(text)
        assert parsed.low == low
        assert parsed.high == high
        assert parsed.avg == avg
        assert parsed.has_value

    def test_months(self):
        assert parse_salary("15-25K·13薪").months == 13
        assert parse_salary("15-25K·14薪").months == 14
        assert parse_salary("15-25K").months == 12

    def test_negotiable(self):
        parsed = parse_salary("面议")
        assert parsed.negotiable
        assert parsed.low is None

    def test_daily_rate_not_monthly(self):
        parsed = parse_salary("300-500元/天")
        assert parsed.is_daily
        assert parsed.low is None

    def test_above_and_below(self):
        assert parse_salary("20K以上").low == 20
        assert parse_salary("15K以下").high == 15

    def test_garbage(self):
        parsed = parse_salary("薪资保密")
        assert parsed.low is None
        assert parsed.high is None

    def test_reversed_range_is_normalized(self):
        parsed = parse_salary("40-20K")
        assert parsed.low == 20
        assert parsed.high == 40


class TestSalaryBucket:
    @pytest.mark.parametrize(
        ("value", "bucket"),
        [
            (None, "薪资未知"),
            (8, "10K 以下"),
            (12, "10-15K"),
            (18, "15-20K"),
            (25, "20-30K"),
            (40, "30-50K"),
            (80, "50K 以上"),
        ],
    )
    def test_bucket(self, value, bucket):
        assert salary_bucket(value) == bucket


class TestUserSalaryExpectation:
    def test_range_with_k(self):
        assert parse_user_salary_expectation("15k-25k") == (15, 25)

    def test_above(self):
        assert parse_user_salary_expectation("20k以上") == (20, None)

    def test_single(self):
        assert parse_user_salary_expectation("20k") == (20, 20)

    def test_blank(self):
        assert parse_user_salary_expectation(None) == (None, None)
        assert parse_user_salary_expectation("面议") == (None, None)


class TestSplitJd:
    def test_splits_duty_and_requirement(self):
        jd = (
            "岗位职责：\n"
            "1. 负责后端开发；\n"
            "2. 参与架构设计。\n\n"
            "任职要求：\n"
            "1. 3 年以上 Python 经验；\n"
            "2. 熟悉 Kubernetes。"
        )
        duty, requirement = split_jd(jd)
        assert duty is not None and "负责后端开发" in duty
        assert requirement is not None and "Kubernetes" in requirement

    def test_no_headings_returns_none(self):
        duty, requirement = split_jd("就是一段没有任何小标题的描述文本。")
        assert duty is None and requirement is None

    def test_empty(self):
        assert split_jd(None) == (None, None)
        assert split_jd("") == (None, None)


class TestTagNormalization:
    def test_split_tags(self):
        assert split_tags("3-5年 | 本科") == ("3-5年", "本科")
        assert split_tags("3-5年") == ("3-5年", None)
        assert split_tags(None) == (None, None)

    def test_unlimited_is_none(self):
        assert normalize_experience("不限") is None
        assert normalize_degree("学历不限") is None
        assert normalize_experience("3-5年") == "3-5年"


class TestSkillExtraction:
    def test_extracts_known_skills(self):
        from bzp_workbench.parsing import extract_skill_keywords

        jd = "熟悉 Python、FastAPI 和 MySQL，了解 Docker 与 Kubernetes，有 Redis 使用经验。"
        skills = extract_skill_keywords(jd)
        for expected in ("Python", "FastAPI", "MySQL", "Docker", "Kubernetes", "Redis"):
            assert expected in skills, f"缺少 {expected}"

    def test_extra_comes_first(self):
        from bzp_workbench.parsing import extract_skill_keywords

        skills = extract_skill_keywords("熟悉 Python", extra=["Elasticsearch"])
        assert skills[0] == "Elasticsearch"

    def test_no_duplicates(self):
        from bzp_workbench.parsing import extract_skill_keywords

        skills = extract_skill_keywords("Python python PYTHON", extra=["Python"])
        assert skills.count("Python") <= 1 or len({s.lower() for s in skills}) == len(skills)

    def test_respects_limit(self):
        from bzp_workbench.parsing import extract_skill_keywords

        jd = "Python Java Golang Rust MySQL Redis Docker Kubernetes Linux Git"
        assert len(extract_skill_keywords(jd, limit=3)) == 3


class TestUtils:
    def test_to_naive_utc(self):
        aware = datetime(2026, 1, 1, 8, 0, tzinfo=timezone(timedelta(hours=8)))
        naive = to_naive_utc(aware)
        assert naive == datetime(2026, 1, 1, 0, 0)
        assert naive.tzinfo is None

    def test_day_bounds_cst(self):
        start, end = day_bounds_cst()
        assert start < end
        assert (end - start) == timedelta(days=1)

    def test_fingerprint_is_stable(self):
        assert fingerprint("a", "b") == fingerprint("a", "b")
        assert fingerprint("a", "b") != fingerprint("a", "c")
        assert len(fingerprint("a")) == 32

    def test_json_roundtrip_and_corrupt(self, tmp_path):
        path = tmp_path / "x.json"
        write_json(path, {"a": 1})
        assert read_json(path) == {"a": 1}

        broken = tmp_path / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        assert read_json(broken, default={"fallback": True}) == {"fallback": True}
        assert read_json(tmp_path / "missing.json", default=None) is None
