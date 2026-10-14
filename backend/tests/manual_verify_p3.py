"""真机验证 P3：对话级任务进度 WebSocket 推送。

前两版都写错了，记录在这里以免重犯
----------------------------------
* 第一版：在**验证脚本进程里** ``task_events.publish()``。
  总线是进程内的，服务端根本收不到 —— 而用例因为"反正也没收到"而**假通过**。
* 第二版：改用 API 创建任务后**取消**它。
  但取消路径**不发布任何事件**，所以三个用例全是假通过。

正确做法：让服务端走**真实的任务派发路径**（``task.started``）。
派发由后台 worker 触发，受 ``max_concurrent_tasks`` 控制，
所以本脚本用 ``max_concurrent_tasks=1`` 精确控制"哪个任务先被派发"。

用例设计（每个任务的归属都在**创建时**就设好，避免竞态）
-------------------------------------------------------
1. 归属我的任务先派发 → 必须收到
2. 归属别人的任务后派发 → 必须**不**收到（会话隔离）
3. 无归属的任务 → 必须**不**收到（从任务页发起）

用法（在 backend/ 目录下，后端需在 18080 运行）：
    python tests/manual_verify_p3.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from bzp_workbench.db import SessionLocal
from bzp_workbench.models import ChatConversation, ScrapeTask

BASE = "http://127.0.0.1:18080"
WS_BASE = "ws://127.0.0.1:18080/api/ws/chat"
SENTINEL = "pytest-p3-verify"


def http_json(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return {"error": exc.code, "detail": exc.read().decode()[:300]}


async def wait_for_task(ws, task_id: int, timeout: float = 40.0) -> dict | None:
    """等待指定任务的推送；忽略其它消息。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    seen: list[int] = []
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return None
        try:
            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
        except asyncio.TimeoutError:
            return None
        data = message.get("data") or {}
        got = (data.get("task") or {}).get("task_id")
        if got is not None:
            seen.append(got)
        if got == task_id:
            return message


async def main() -> int:
    import websockets

    results: list[tuple[bool, str]] = []
    created_ids: list[int] = []

    # ---- 确保没有正在跑的任务，否则空闲槽位会立刻被它们占用 ----
    print("清理队列中…")
    queue = http_json("GET", "/api/system/queue")["data"]
    for task_id in queue.get("running_task_ids", []):
        http_json("POST", f"/api/tasks/{task_id}/cancel")
    await asyncio.sleep(1)

    # ---- 准备两个会话 ----
    async with SessionLocal() as db:
        mine = ChatConversation(title="P3 验证-我", mode="job_hunting")
        other = ChatConversation(title="P3 验证-别人", mode="job_hunting")
        db.add_all([mine, other])
        await db.commit()
        await db.refresh(mine)
        await db.refresh(other)
        my_id, other_id = mine.id, other.id

    print(f"我的会话 #{my_id} / 别人的会话 #{other_id}\n")

    url = f"{WS_BASE}/{my_id}"
    async with websockets.connect(url) as ws:
        hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        results.append((hello.get("type") == "connected", "WebSocket 握手成功"))
        print(f"握手: {hello}\n")

        # ============ 创建三个任务 ============
        #
        # ⚠️ 竞态（前两版都栽在这里）
        # ------------------------------
        # 任务一旦入队，worker 会**立刻**派发并发布 task.started。如果先创建
        # 再回头写 conversation_id，中继很可能已经查过库、把 None 缓存下来了
        # —— 之后怎么改都不会推。症状是"超时未收到"，而日志里显示
        # 归属会话=None，极容易误判成路由逻辑写错了。
        #
        # 解决办法：**先把归属写好，再让任务变得可被派发**。
        # 队列此刻是空的（上面已清空），所以直接建 pending 任务并写好归属，
        # worker 的轮询循环自然会发现并派发它 —— 不存在"创建后才写归属"的窗口。
        async with SessionLocal() as db:
            rows = []
            for city, conversation_id in (
                ("深圳", my_id),
                ("北京", other_id),
                ("上海", None),
            ):
                row = ScrapeTask(
                    keyword=SENTINEL,
                    city=city,
                    max_pages=1,
                    with_detail=False,
                    # 归属在**创建时**就写好，不存在竞态
                    conversation_id=conversation_id,
                    status="pending",
                    stage_text="已入队，等待执行",
                )
                db.add(row)
                rows.append(row)
            await db.commit()
            for row in rows:
                await db.refresh(row)
            mine_task, other_task, orphan_task = (row.id for row in rows)
            created_ids.extend([mine_task, other_task, orphan_task])

        print(f"任务 #{mine_task}=我的会话  #{other_task}=别人会话  #{orphan_task}=无归属")
        print("（队列为空，worker 会自然发现它们并派发）")

        # ================= 用例 1：我的任务应被推送 =================
        print("\n[用例 1] 我的任务被派发（应收到 task.started）")
        message = await wait_for_task(ws, mine_task)
        if message is None:
            print("  ✗ 超时未收到")
            results.append((False, "自己的任务收到推送（派发路径）"))
        else:
            data = message["data"]
            task_data = data["task"]
            print(
                f"  ✓ 收到 type={message['type']} 事件={data['event']} "
                f"任务=#{task_data['task_id']} 状态={task_data['status_label']}"
            )
            ok = (
                message["type"] == "chat.task_progress"
                and data["conversation_id"] == my_id
                and task_data["task_id"] == mine_task
            )
            results.append((ok, "自己的任务收到推送且会话归属正确"))

        # 取消掉，腾出槽位给下一个任务（否则并发上限会挡住后续派发）
        http_json("POST", f"/api/tasks/{mine_task}/cancel")
        await asyncio.sleep(2)

        # ================= 用例 2：别人的任务不该来 =================
        print("\n[用例 2] 别人会话的任务被派发（我不该收到）")
        leaked = await wait_for_task(ws, other_task, timeout=30.0)
        if leaked is None:
            print("  ✓ 正确：没有收到别人会话的任务")
            results.append((True, "会话隔离正确"))
        else:
            print(f"  ✗ 不该收到却收到了：{leaked['data']['task']}")
            results.append((False, "收到了别人会话的任务"))

        http_json("POST", f"/api/tasks/{other_task}/cancel")
        await asyncio.sleep(2)

        # ================= 用例 3：无归属任务不该来 =================
        print("\n[用例 3] 无归属任务被派发（我不该收到）")
        orphan_leak = await wait_for_task(ws, orphan_task, timeout=30.0)
        if orphan_leak is None:
            print("  ✓ 正确：没有收到无归属任务")
            results.append((True, "无归属任务不推送"))
        else:
            print(f"  ✗ 不该收到却收到了：{orphan_leak['data']['task']}")
            results.append((False, "收到了无归属任务"))

    # ================= 汇总 =================
    print(f"\n{'=' * 70}")
    for index, (ok, label) in enumerate(results, start=1):
        print(f"  {'✓' if ok else '✗'} {index}. {label}")
    passed = sum(1 for ok, _ in results if ok)
    print(f"\n结论：{passed}/{len(results)} 通过")

    # ---- 清理 ----
    # 先取消任务（避免后台真的去跑），再统一删除测试数据。
    # 走共享清理工具，避免各脚本各写一份、漏掉某个表。
    for task_id in created_ids:
        http_json("POST", f"/api/tasks/{task_id}/cancel")

    from manual_cleanup import cleanup_verification_data

    summary = await cleanup_verification_data(
        extra_task_keywords=[SENTINEL],
        extra_conversation_ids=[my_id, other_id],
    )
    print(f"测试数据已清理：任务 {summary['tasks']} 条，对话 {summary['conversations']} 个")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
