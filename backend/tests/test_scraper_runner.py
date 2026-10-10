"""单元测试：爬虫适配层（失败归类、结果归一化、环境构造）。"""

from __future__ import annotations

import json

import pytest

from bzp_workbench.services.scraper_runner import (
    REASON_CDP,
    REASON_CITY,
    REASON_LOGIN,
    REASON_RISK,
    REASON_UNKNOWN,
    build_environment,
    check_scraper_available,
    classify_failure,
    cdp_reachable,
    merge_jobs_with_details,
    normalize_job,
    read_scraper_version,
)


class TestClassifyFailure:
    def test_login_required(self):
        assert classify_failure("页面出现「登录查看完整内容」", 1) == REASON_LOGIN
        assert classify_failure("登录态已失效，请重新登录", 1) == REASON_LOGIN
        assert classify_failure("未登录", None) == REASON_LOGIN

    def test_risk_control(self):
        assert classify_failure('接口返回 {"code": 31, "message": "环境存在异常"}', 2) == REASON_RISK
        assert classify_failure("访问频繁，请稍后再试", 2) == REASON_RISK
        assert classify_failure("跳转到 _security_check 页面", 1) == REASON_RISK
        assert classify_failure("请完成验证码", 1) == REASON_RISK

    def test_risk_takes_priority_over_login(self):
        """同时出现登录与风控字样时，按更严重的风控处理（不重试）。"""
        text = "环境存在异常\n登录查看完整内容"
        assert classify_failure(text, 1) == REASON_RISK

    def test_city_unresolved(self):
        assert classify_failure("无法识别城市：不存在市", 1) == REASON_CITY

    def test_cdp_unavailable(self):
        assert (
            classify_failure(
                "ConnectionError: HTTPConnectionPool(host='127.0.0.1', port=9222)", 1
            )
            == REASON_CDP
        )

    def test_unknown(self):
        assert classify_failure("", 1) == REASON_UNKNOWN
        assert classify_failure("某种没见过的错误", 1) == REASON_UNKNOWN

    def test_ansi_codes_are_stripped(self):
        assert classify_failure("\x1b[31m访问频繁\x1b[0m", 2) == REASON_RISK


class TestNormalizeJob:
    def test_basic_mapping(self):
        job = {
            "title": "Python后端工程师",
            "salary": "20-40K·14薪",
            "location": "深圳·南山区·科技园",
            "tags": "3-5年 | 本科",
            "boss_name": "示例科技",
            "boss_title": "技术总监",
            "company_scale": "500-999人",
            "company_stage": "B轮",
            "company_industry": "互联网",
            "skills": "Python | FastAPI",
            "job_labels": "五险一金",
            "job_link": "https://www.zhipin.com/job_detail/abc.html",
            "company_link": "https://www.zhipin.com/gongsi/brand.html",
            "security_id": "sec",
            "lid": "lid",
            "job_id": "abcdef1234567890",
            "is_new": True,
        }
        record = normalize_job(job)

        assert record["title"] == "Python后端工程师"
        assert record["company"] == "示例科技"
        # 薪资归一化到千元/月
        assert record["salary_min"] == 20
        assert record["salary_max"] == 40
        assert record["salary_avg"] == 30
        # 城市从 location 第一段拆出
        assert record["city"] == "深圳"
        # 经验/学历从 tags 拆出
        assert record["experience"] == "3-5年"
        assert record["degree"] == "本科"
        # 技能标签是 JSON 数组字符串
        assert set(json.loads(record["skill_tags"])) >= {"Python", "FastAPI", "五险一金"}
        # 跳转链接原样保留
        assert record["job_link"].endswith("abc.html")
        assert record["is_new"] is True
        # 没给 JD 时 detail_fetched 为 False
        assert record["detail_fetched"] is False

    def test_detail_merged_in(self):
        job = {
            "title": "前端工程师",
            "salary": "15-25K",
            "boss_name": "某公司",
            "job_id": "id-1",
            "job_link": "https://www.zhipin.com/job_detail/id-1.html",
        }
        detail = {
            "job_id": "id-1",
            "jd": "岗位职责：\n1. 负责前端开发。\n\n任职要求：\n1. 熟悉 Vue3。",
            "skill_tags": ["Vue3", "TypeScript"],
            "boss_active_status": "刚刚活跃",
        }
        record = normalize_job(job, detail)

        assert record["detail_fetched"] is True
        assert "负责前端开发" in record["job_duty"]
        assert "熟悉 Vue3" in record["job_requirement"]
        assert "Vue3" in json.loads(record["skill_tags"])
        assert "Vue3" in json.loads(record["skill_keywords"])
        # 详情页的活跃状态优先
        assert record["boss_active_status"] == "刚刚活跃"

    def test_negotiable_salary(self):
        record = normalize_job({"title": "岗位", "salary": "面议", "job_id": "x"})
        assert record["salary"] == "面议"
        assert record["salary_avg"] is None

    def test_missing_job_id_falls_back_to_fingerprint(self):
        record = normalize_job({"title": "无ID岗位", "boss_name": "公司"})
        assert record["job_id"]
        assert len(record["job_id"]) == 32

    def test_defaults_for_empty_title_and_company(self):
        record = normalize_job({"job_id": "y"})
        assert record["title"] == "未命名岗位"
        assert record["company"] == "未知公司"

    def test_unlimited_experience_is_none(self):
        record = normalize_job({"title": "岗位", "tags": "不限 | 不限", "job_id": "z"})
        assert record["experience"] is None
        assert record["degree"] is None

    def test_json_fields_are_valid_json(self):
        record = normalize_job(
            {
                "title": "岗位",
                "job_id": "j",
                "jd": "熟悉 Python 与 Docker",
                "skills": "Python | Docker",
            }
        )
        # 数据库里存的是 JSON 数组字符串，必须能被 json.loads 解析
        assert json.loads(record["skill_tags"]) == ["Python", "Docker"]
        keywords = json.loads(record["skill_keywords"])
        assert "Python" in keywords and "Docker" in keywords
        # raw_payload 保存原始 JSON，便于回溯爬虫字段变化
        assert isinstance(json.loads(record["raw_payload"]), dict)

    def test_empty_list_fields_are_none(self):
        """空列表存 NULL 而不是 '[]'，避免前端渲染空标签组。"""
        record = normalize_job({"title": "岗位", "job_id": "k"})
        assert record["skill_tags"] is None
        assert record["skill_keywords"] is None


class TestMergeJobsWithDetails:
    def test_merges_by_job_id(self):
        jobs = [
            {"job_id": "a", "title": "岗位A", "boss_name": "公司A"},
            {"job_id": "b", "title": "岗位B", "boss_name": "公司B"},
        ]
        details = {
            "a": {"job_id": "a", "jd": "岗位职责：\n做A的事情。", "skill_tags": ["Python"]},
        }
        merged = merge_jobs_with_details(jobs, details)
        assert len(merged) == 2
        assert merged[0]["detail_fetched"] is True
        assert merged[1]["detail_fetched"] is False

    def test_merges_by_link_when_no_job_id(self):
        jobs = [{"title": "岗位", "job_link": "https://x/1.html", "job_id": "k1"}]
        details = {"https://x/1.html": {"jd": "岗位职责：\n做事。", "skill_tags": []}}
        merged = merge_jobs_with_details(jobs, details)
        assert merged[0]["detail_fetched"] is True


class TestEnvironment:
    def test_home_is_redirected_to_storage(self, storage_root):
        env = build_environment("testhash")
        # 关键：爬虫的 ~ 被重定向到工作台 storage，登录态因此隔离
        assert env.env["HOME"] == str(storage_root / "chrome-profiles")
        assert env.env["USERPROFILE"] == env.env["HOME"]
        assert env.env["PYTHONUNBUFFERED"] == "1"
        assert env.profile_dir == storage_root / "chrome-profiles" / "testhash"
        assert env.profile_dir.is_dir()

    def test_env_does_not_leak_host_home(self, storage_root):
        env = build_environment("another")
        assert env.env["HOME"] != str(storage_root)


class TestScraperDiscovery:
    def test_check_scraper_available_with_fake(self):
        available, message = check_scraper_available()
        assert available is True, message

    def test_read_version_from_source(self):
        version = read_scraper_version()
        assert version is not None
        assert version.startswith("2.3")

    def test_cdp_reachable_false_for_unused_port(self):
        # 端口 1 上不会有服务监听
        assert cdp_reachable(1, timeout=0.2) is False
