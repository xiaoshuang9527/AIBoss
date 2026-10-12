"""P1 冒烟自检：工具注册、Prompt 选择、路由解析。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bzp_workbench.main as main  # noqa: F401  确保应用可导入
from bzp_workbench.services.ai.router import detect_explicit_switch, parse_router_output
from bzp_workbench.services.ai.service import DEFAULT_MODE, system_prompt_for
from bzp_workbench.services.ai.tools import tool_names, tools_for_mode


def main_check() -> int:
    print("应用导入: OK")
    print(f"默认模式: {DEFAULT_MODE}")
    print(f"已注册工具: {tool_names()}")
    print()

    print("按模式分配工具:")
    for mode in ("job_hunting", "interview_coach", "chat"):
        names = [t.name for t in tools_for_mode(mode)]
        print(f"  {mode:16} -> {names}")
    print()

    print("按模式分配 Prompt:")
    for mode in ("job_hunting", "interview_coach", "chat"):
        prompt = system_prompt_for(mode)
        print(f"  {mode:16} -> {len(prompt)} 字")
    print()

    print("路由输出解析（容错各种脏输出）:")
    cases = [
        ('{"intent": "job_hunting", "reason": "提到岗位", "confidence": 0.9}', "标准 JSON"),
        ('```json\n{"intent": "interview_coach", "confidence": 0.8}\n```', "markdown 包裹"),
        ('我认为是 job_hunting 意图', "裸词提及"),
        ('{"intent": "not_a_mode"}', "非法模式名"),
        ("完全无法解析的内容", "垃圾输入"),
        ("", "空输入"),
    ]
    for raw, label in cases:
        mode, reason, conf = parse_router_output(raw)
        print(f"  {label:14} -> mode={mode:16} conf={conf:.1f}  {reason[:24]}")
    print()

    print("显式模式切换识别:")
    for text in ("切换到找岗位", "帮我准备面试", "普通对话模式", "深圳有什么岗位", "我想改简历"):
        print(f"  {text:16} -> {detect_explicit_switch(text)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main_check())
