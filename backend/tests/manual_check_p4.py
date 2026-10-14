"""P4 自检：工具注册与报告解析容错。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bzp_workbench.services.ai.tools import tool_names, tools_for_mode
from bzp_workbench.services.ai.tools.match import parse_report


def main() -> int:
    print("全部工具:", tool_names())
    for mode in ("job_hunting", "interview_coach", "chat"):
        names = [t.name for t in tools_for_mode(mode)]
        print(f"  {mode:16} -> {names}")
    print()

    print("报告解析容错：")
    cases = [
        ('{"verdict": "还行", "overall_score": 72, "questions": []}', "标准 JSON"),
        ('```json\n{"overall_score": "80"}\n```', "markdown 包裹 + 字符串分数"),
        ('前面废话 {"overall_score": 150} 后面废话', "夹带文字 + 分数越界"),
        ("完全不是 JSON", "垃圾输入"),
        (
            '{"overall_score": 60, "dimensions": [{"name": "技能匹配", "level": "不匹配"}]}',
            "中文 level",
        ),
        (
            '{"overall_score": 55, "risks": ["跳槽频繁"], "questions": ["介绍一下你自己"]}',
            "风险/题目是纯字符串",
        ),
        ("", "空输入"),
    ]
    for raw, label in cases:
        report = parse_report(raw)
        if report is None:
            print(f"  {label:26} -> None（降级为纯文本）")
            continue
        dims = report["dimensions"]
        level0 = dims[0]["level"] if dims else "-"
        print(
            f"  {label:26} -> score={report['overall_score']:3} "
            f"维度数={len(dims)} 维度1level={level0} "
            f"风险数={len(report['risks'])} 题目数={len(report['questions'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
