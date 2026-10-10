"""集成测试：API 路由 + 假爬虫端到端调度。

覆盖：
* 概览接口（4 张卡片结构）
* 任务创建 → 队列执行 → 岗位入库 → 结果查询 → 导出
* 登录态失效 / 风控命中时的失败归类与「待处理」状态
* 队列并发上限
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest


async def _wait_for_task(client, task_id: int, timeout: float = 40.0) -> dict:
    """轮询直到任务进入终态。"""
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict = {}
    while asyncio.get_event_loop().time() < deadline:
        response = await client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200
        last = response.json()["data"]
        if last["status"] in ("succeeded", "failed", "cancelled", "needs_attention"):
            return last
        await asyncio.sleep(0.25)
    raise AssertionError(f"任务 {task_id} 未在 {timeout}s 内结束，最后状态：{last}")


async def _drain_queue(client, timeout: float = 60.0) -> None:
    """等待队列空闲，避免上一个测试留下的任务污染下一个测试的数据。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        data = (await client.get("/api/tasks/queue")).json()["data"]
        if data["running"] == 0 and data["pending"] == 0:
            return
        await asyncio.sleep(0.3)
    raise AssertionError("队列未在超时时间内排空")


def _set_mode(mode: str | None) -> None:
    if mode is None:
        os.environ.pop("FAKE_SCRAPER_MODE", None)
    else:
        os.environ["FAKE_SCRAPER_MODE"] = mode


class TestMeta:
    async def test_root(self, client):
        response = await client.get("/")
        assert response.status_code == 200
        body = response.json()
        assert "disclaimer" in body

    async def test_health(self, client):
        response = await client.get("/api/system/health")
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "ok"

    async def test_openapi_available(self, client):
        response = await client.get("/openapi.json")
        assert response.status_code == 200
        assert "/api/tasks" in response.json()["paths"]


class TestDashboard:
    async def test_dashboard_structure(self, client):
        response = await client.get("/api/system/dashboard")
        assert response.status_code == 200
        data = response.json()["data"]

        # 4 张卡片
        for key in ("total_jobs", "today_new_jobs", "active_tasks", "auth_status"):
            assert key in data["cards"], f"缺少卡片字段 {key}"

        # 图表与最近任务
        assert "salary_distribution" in data
        assert "experience_distribution" in data
        assert "daily_trend" in data
        assert isinstance(data["recent_tasks"], list)

        # 免责声明必须存在
        assert data["disclaimer"]
        assert data["status_text"]

    async def test_no_billing_concepts(self, client):
        """合规红线：不得出现任何额度 / 计费 / 套餐字样。"""
        forbidden = ("额度", "计费", "套餐", "充值", "付费", "会员", "quota", "billing", "credit")
        for path in ("/api/system/dashboard", "/api/system/settings"):
            response = await client.get(path)
            blob = json.dumps(response.json(), ensure_ascii=False).lower()
            for word in forbidden:
                assert word not in blob, f"{path} 响应中出现了禁用词：{word}"


class TestScraperHealth:
    async def test_health_reports_fake_scraper(self, client):
        response = await client.get("/api/system/scraper-health")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["entry_exists"] is True
        assert data["version"] is not None


class TestTaskLifecycle:
    async def test_create_task_requires_keyword(self, client):
        response = await client.post("/api/tasks", json={"keyword": "  ", "city": "深圳"})
        assert response.status_code == 422

    async def test_create_task_caps_pages(self, client):
        """页数超限会被夹取到硬上限，且不真正执行抓取。"""
        response = await client.post(
            "/api/tasks", json={"keyword": "页数上限", "city": "深圳", "max_pages": 99}
        )
        assert response.status_code == 200
        task = response.json()["data"]["task"]
        # 硬上限 10 页
        assert task["max_pages"] == 10

        # 取消掉，避免它在后台真跑起来
        cancelled = await client.post(f"/api/tasks/{task['id']}/cancel")
        assert cancelled.status_code == 200
        await _wait_for_task(client, task["id"])
        await _drain_queue(client)

    async def test_full_scrape_flow(self, client):
        _set_mode(None)
        await _drain_queue(client)
        response = await client.post(
            "/api/tasks",
            json={
                "keyword": "Python后端",
                "city": "深圳",
                "salary": "15k-25k",
                "experience": "104",
                "max_pages": 2,
                "with_detail": True,
            },
        )
        assert response.status_code == 200
        task_id = response.json()["data"]["task"]["id"]

        final = await _wait_for_task(client, task_id)
        assert final["status"] == "succeeded", final
        assert final["total_found"] == 6  # 2 页 × 3 条
        assert final["new_count"] == 6
        assert final["detail_count"] == 6
        assert final["progress"] == 100

        # ---- 岗位结果 ----
        jobs_response = await client.get(f"/api/jobs?task_id={task_id}")
        assert jobs_response.status_code == 200
        payload = jobs_response.json()["data"]
        assert payload["meta"]["total"] == 6

        job = payload["items"][0]
        # 跳转 BOSS 原页面所需的链接必须存在
        assert job["job_link"].startswith("https://www.zhipin.com/job_detail/")
        # 岗位详情全文
        assert job["jd"] and "岗位职责" in job["jd"]
        assert job["job_duty"] and job["job_requirement"]
        # 技能标签
        assert job["skill_keywords"]
        # 薪资归一化
        assert job["salary_avg"] is not None

        # ---- 岗位详情 ----
        detail_response = await client.get(f"/api/jobs/{job['id']}")
        assert detail_response.status_code == 200
        assert detail_response.json()["data"]["job_id"] == job["job_id"]

        # ---- 任务日志 ----
        log_response = await client.get(f"/api/tasks/{task_id}/logs")
        assert log_response.status_code == 200
        assert log_response.json()["data"]["lines"]

        # ---- 任务聚合 ----
        summary = await client.get(f"/api/tasks/{task_id}/summary")
        assert summary.status_code == 200
        assert summary.json()["data"]["total_jobs"] == 6

        # ---- 筛选 ----
        filtered = await client.get("/api/jobs?keyword=Python后端&city=深圳")
        assert filtered.json()["data"]["meta"]["total"] == 6

        none_match = await client.get("/api/jobs?city=不存在的城市")
        assert none_match.json()["data"]["meta"]["total"] == 0

        # ---- 导出 ----
        csv_response = await client.get(f"/api/export/jobs?fmt=csv&task_id={task_id}")
        assert csv_response.status_code == 200
        assert csv_response.headers["content-type"].startswith("text/csv")
        text = csv_response.content.decode("utf-8")
        assert text.startswith("\ufeff")  # BOM，保证 Excel 不乱码
        assert "岗位详情全文" in text
        assert "Python后端工程师" in text

        json_response = await client.get(f"/api/export/jobs?fmt=json&task_id={task_id}")
        assert json_response.status_code == 200
        exported = json.loads(json_response.content.decode("utf-8"))
        assert len(exported) == 6

        # ---- 收藏 ----
        fav = await client.post(f"/api/jobs/{job['id']}/favorite", json={"favorite": True})
        assert fav.status_code == 200
        assert fav.json()["data"]["is_favorite"] is True

        # ---- 概览卡片应反映数据 ----
        dashboard = (await client.get("/api/system/dashboard")).json()["data"]
        assert dashboard["cards"]["total_jobs"] == 6
        assert dashboard["cards"]["today_new_jobs"] == 6
        assert dashboard["cards"]["favorite_jobs"] == 1
        assert dashboard["cards"]["succeeded_tasks"] >= 1

    async def test_duplicate_jobs_are_deduped(self, client):
        """同样关键词跑两次，岗位不应重复入库。"""
        _set_mode(None)
        first = await client.post(
            "/api/tasks", json={"keyword": "去重测试", "city": "杭州", "max_pages": 1}
        )
        await _wait_for_task(client, first.json()["data"]["task"]["id"])

        second = await client.post(
            "/api/tasks", json={"keyword": "去重测试", "city": "杭州", "max_pages": 1}
        )
        final = await _wait_for_task(client, second.json()["data"]["task"]["id"])
        assert final["status"] == "succeeded"
        # 第二次抓的是同样的 3 条，应该 0 新增
        assert final["new_count"] == 0
        assert final["total_found"] == 3

        listing = (await client.get("/api/jobs?keyword=去重测试")).json()["data"]
        assert listing["meta"]["total"] == 3
        await _drain_queue(client)

    async def test_login_failure_needs_attention(self, client):
        _set_mode("login")
        response = await client.post(
            "/api/tasks", json={"keyword": "登录失败", "city": "北京", "max_pages": 1}
        )
        final = await _wait_for_task(client, response.json()["data"]["task"]["id"])
        assert final["status"] == "needs_attention"
        assert "登录态" in final["error_message"]

        # 授权档案应同步变为失效
        status = (await client.get("/api/auth/status")).json()["data"]
        assert status["profile"]["status"] == "expired"
        await _drain_queue(client)

    async def test_risk_control_failure_needs_attention(self, client):
        _set_mode("risk")
        response = await client.post(
            "/api/tasks", json={"keyword": "风控测试", "city": "北京", "max_pages": 1}
        )
        final = await _wait_for_task(client, response.json()["data"]["task"]["id"])
        assert final["status"] == "needs_attention"
        assert "风控" in final["error_message"]
        await _drain_queue(client)

    async def test_empty_result_is_failure(self, client):
        _set_mode("empty")
        response = await client.post(
            "/api/tasks", json={"keyword": "空结果", "city": "广州", "max_pages": 1}
        )
        final = await _wait_for_task(client, response.json()["data"]["task"]["id"])
        assert final["status"] == "failed"
        await _drain_queue(client)

    async def test_retry_and_cancel_endpoints(self, client):
        _set_mode("login")
        created = await client.post(
            "/api/tasks", json={"keyword": "重试测试", "city": "成都", "max_pages": 1}
        )
        task_id = created.json()["data"]["task"]["id"]
        await _wait_for_task(client, task_id)

        # 重试：状态回到 pending 并重新执行
        retried = await client.post(f"/api/tasks/{task_id}/retry")
        assert retried.status_code == 200
        assert retried.json()["data"]["task"]["status"] == "pending"
        await _wait_for_task(client, task_id)

        # 删除
        deleted = await client.delete(f"/api/tasks/{task_id}")
        assert deleted.status_code == 200
        assert (await client.get(f"/api/tasks/{task_id}")).status_code == 404
        await _drain_queue(client)

    async def test_task_listing_and_queue(self, client):
        listing = await client.get("/api/tasks?page=1&page_size=5")
        assert listing.status_code == 200
        assert "meta" in listing.json()["data"]

        queue = await client.get("/api/tasks/queue")
        assert queue.status_code == 200
        data = queue.json()["data"]
        assert data["max_concurrent"] >= 1
        assert "worker_alive" in data


class TestAuthEndpoints:
    async def test_profiles_created(self, client):
        response = await client.get("/api/auth/profiles")
        assert response.status_code == 200
        profiles = response.json()["data"]
        assert len(profiles) >= 1
        assert profiles[0]["status_label"]

    async def test_session_current_is_null_when_idle(self, client):
        response = await client.get("/api/auth/sessions/current")
        assert response.status_code == 200
        # 会话可能为 None（没有正在进行的授权）
        assert "data" in response.json()

    async def test_logout_marks_expired(self, client):
        response = await client.post("/api/auth/logout")
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "expired"


class TestSettings:
    async def test_settings_roundtrip(self, client):
        current = (await client.get("/api/system/settings")).json()["data"]
        assert current["max_concurrent_tasks"] >= 1

        updated = await client.put(
            "/api/system/settings", json={"max_concurrent_tasks": 9, "task_delay_min": 2}
        )
        assert updated.status_code == 200
        data = updated.json()["data"]
        # 并发被夹取到硬上限 4
        assert data["max_concurrent_tasks"] == 4
        assert data["task_delay_min"] == 2

        # 复原，避免影响其他测试
        await client.put("/api/system/settings", json={"max_concurrent_tasks": 2, "task_delay_min": 0})

    async def test_logs_listing(self, client):
        response = await client.get("/api/system/logs?page=1&page_size=10")
        assert response.status_code == 200
        assert "items" in response.json()["data"]

    async def test_storage_usage(self, client):
        response = await client.get("/api/system/storage")
        assert response.status_code == 200
        assert "total_bytes" in response.json()["data"]


class TestJobFacets:
    async def test_facets(self, client):
        response = await client.get("/api/jobs/facets")
        assert response.status_code == 200
        data = response.json()["data"]
        assert "cities" in data and "skills" in data
        assert isinstance(data["cities"], list)

    async def test_top_skills(self, client):
        response = await client.get("/api/jobs/top-skills?limit=5")
        assert response.status_code == 200
        assert isinstance(response.json()["data"], list)
