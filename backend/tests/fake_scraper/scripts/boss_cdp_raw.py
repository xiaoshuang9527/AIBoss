"""测试用的假爬虫。

模拟 ``boss-zhipin-scraper`` 的命令行契约，让工作台的调度链路
（子进程 → 日志 → JSON 结果 → 入库）可以在没有 Chrome / 没有网络的情况下
被完整验证。

行为：
* ``--check``          : 打印检查结果；设置环境变量 ``FAKE_SCRAPER_CHECK=fail`` 可模拟未登录
* ``--setup-chrome``   : 直接打印「已登录」并退出
* 抓取模式              : 按关键词生成 N 条岗位 + 详情，写入 --output / --detail-output
                        环境变量 ``FAKE_SCRAPER_MODE``：
                          ok（默认） / login / risk / crash / empty
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime

__version__ = "2.3.0-fake"


def _job(index: int, keyword: str, city: str) -> dict:
    encrypt_job_id = hashlib.md5(f"{keyword}-{city}-{index}".encode()).hexdigest()
    job_link = f"https://www.zhipin.com/job_detail/{encrypt_job_id}.html"
    salaries = ["12-18K", "15-25K", "20-40K·14薪", "8-12K", "30-50K", "面议"]
    experiences = ["1-3年", "3-5年", "5-10年", "经验不限"]
    degrees = ["本科", "大专", "硕士", "不限"]
    return {
        "title": f"{keyword}工程师-{index}",
        "salary": salaries[index % len(salaries)],
        "salary_source": "api",
        "location": f"{city}·南山区·科技园",
        "tags": f"{experiences[index % 4]} | {degrees[index % 4]}",
        "boss_name": f"示例公司{index % 3}",
        "boss_title": "技术负责人",
        "boss_active_status": "在线",
        "company_scale": "100-499人",
        "company_stage": "B轮",
        "company_industry": "互联网",
        "job_labels": "五险一金 | 双休",
        "skills": "Python | FastAPI | MySQL",
        "security_id": f"sec-{index}",
        "lid": f"lid-{index}",
        "encrypt_job_id": encrypt_job_id,
        "encrypt_boss_id": f"boss-{index}",
        "encrypt_brand_id": f"brand-{index % 3}",
        "job_link": job_link,
        "company_link": f"https://www.zhipin.com/gongsi/brand-{index % 3}.html",
        "welfare": "五险一金 | 定期体检",
        "job_id": hashlib.md5(job_link.encode()).hexdigest()[:16],
        "is_new": index % 2 == 0,
    }


def _detail(job: dict) -> dict:
    jd = (
        "岗位职责：\n"
        "1. 负责核心业务系统的设计与开发；\n"
        "2. 参与需求评审，输出技术方案；\n"
        "3. 持续优化系统性能与稳定性。\n\n"
        "任职要求：\n"
        "1. 熟悉 Python / FastAPI / MySQL / Redis；\n"
        "2. 了解 Docker、Kubernetes 与微服务架构；\n"
        "3. 具备良好的沟通能力与团队协作意识。"
    )
    return {
        "job_id": job["job_id"],
        "title": job["title"],
        "company": job["boss_name"],
        "salary": job["salary"],
        "salary_source": job["salary_source"],
        "location": job["location"],
        "boss_active_status": job["boss_active_status"],
        "tags_list": job["tags"],
        "job_link": job["job_link"],
        "link": job["job_link"],
        "skill_tags": ["Python", "FastAPI", "MySQL", "Redis", "Docker"],
        "jd": jd,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="fake boss scraper")
    parser.add_argument("--keyword", default="Python")
    parser.add_argument("--city", default="深圳")
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--output")
    parser.add_argument("--detail-output")
    parser.add_argument("--cdp-port", type=int, default=9222)
    parser.add_argument("--format", default="json")
    parser.add_argument("--detail", dest="detail", action="store_true")
    parser.add_argument("--no-detail", dest="detail", action="store_false")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--setup-chrome", action="store_true")
    parser.add_argument("--setup-edge", action="store_true")
    parser.add_argument("--no-wait-login", action="store_true")
    parser.add_argument("--login-timeout", type=int, default=300)
    parser.add_argument("--copy-login-state", action="store_true")
    parser.add_argument("--reset-chrome-profile", action="store_true")
    parser.add_argument("--allow-dom-fallback", action="store_true")
    parser.add_argument("--analysis", action="store_true")
    parser.add_argument("--version", action="version", version=__version__)
    args, _unknown = parser.parse_known_args(argv)

    mode = os.environ.get("FAKE_SCRAPER_MODE", "ok")

    if args.setup_chrome or args.setup_edge:
        print("已启动专用浏览器，等待登录…")
        print("登录成功，接口返回明文薪资")
        return 0

    if args.check:
        check_mode = os.environ.get("FAKE_SCRAPER_CHECK", "ok")
        if check_mode == "fail":
            print("环境检查：未登录")
            return 1
        if check_mode == "risk":
            print("接口返回 code: 37 您的环境存在异常")
            return 2
        print("环境检查：登录态正常，通过")
        return 0

    if mode == "login":
        print("页面出现「登录查看完整内容」")
        return 1
    if mode == "risk":
        print('接口返回 {"code": 31, "message": "您的环境存在异常，请稍后再试"}')
        return 2
    if mode == "crash":
        raise RuntimeError("模拟爬虫崩溃")

    total_pages = max(1, min(args.pages, 10))
    jobs: list[dict] = []
    for page_index in range(1, total_pages + 1):
        print(f"=== 第 {page_index} 页 ===")
        page_jobs = [_job(page_index * 100 + i, args.keyword, args.city) for i in range(3)]
        jobs.extend(page_jobs)
        for job in page_jobs:
            marker = "🆕 " if job["is_new"] else ""
            print(f"  ✓ {marker}{job['title']} | {job['salary']} | {job['location']}")
        print(f"  本页 {len(page_jobs)} 条, 新增 {len(page_jobs)}, 累计 {len(jobs)}")

    payload = {
        "keyword": args.keyword,
        "city": args.city,
        "filters": {},
        "filter_desc": "",
        "scraped_at": datetime.now().isoformat(),
        "total": len(jobs),
        "jobs": jobs,
    }
    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    if args.detail and args.detail_output:
        details = [_detail(job) for job in jobs]
        print(f"\n=== 抓取岗位详情 ({len(details)} 个) ===\n")
        for index, detail in enumerate(details, start=1):
            print(f"[{index}/{len(details)}] {detail['company']} - {detail['title']}")
            print("  加载页面...")
        os.makedirs(os.path.dirname(args.detail_output) or ".", exist_ok=True)
        with open(args.detail_output, "w", encoding="utf-8") as handle:
            json.dump(details, handle, ensure_ascii=False, indent=2)

    if mode == "empty":
        if args.output:
            payload["jobs"] = []
            payload["total"] = 0
            with open(args.output, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
        print("无数据")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
