"""``boss-zhipin-scraper`` 调度封装。

职责边界
--------
* **只用公开 CLI**：一律通过 ``scripts/boss_cdp_raw.py`` 的命令行接口调用，
  不 import 它的内部函数，这样上游脚本重构不会把工作台带崩。
* **登录态复用**：把 ``HOME``/``USERPROFILE`` 指向工作台自有的 storage 目录，
  爬虫于是使用 ``<storage>/chrome-profiles/<hash>`` 作为隔离 profile，
  既复用了扫码登录态，又完全不碰用户主浏览器。
* **异步与并发控制**：用 ``asyncio`` 子进程执行，输出落到日志文件后轮询，
  避免管道缓冲区写满导致子进程阻塞。
* **反爬与重试**：解析爬虫输出判断失败原因；登录态失效和风控拦截
  **不重试**（重试只会加重风控），网络抖动/超时才重试。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import settings
from ..parsing import (
    extract_skill_keywords,
    normalize_degree,
    normalize_experience,
    parse_salary,
    split_jd,
    split_tags,
)
from ..utils import read_json, strip_ansi, tail_lines, utcnow

ProgressCallback = Callable[[int, str], Awaitable[None]]

# ------------------------------------------------------------------
# 失败归类
# ------------------------------------------------------------------
REASON_LOGIN = "login_required"
REASON_RISK = "risk_control"
REASON_TIMEOUT = "timeout"
REASON_CDP = "cdp_unavailable"
REASON_CITY = "city_unresolved"
REASON_UNKNOWN = "unknown"

REASON_TEXT: dict[str, str] = {
    REASON_LOGIN: "BOSS 直聘登录态失效，请到「账号授权」重新扫码登录",
    REASON_RISK: "触发 BOSS 直聘风控校验（环境异常/访问频繁），请降低抓取频率后稍后重试",
    REASON_TIMEOUT: "抓取超时被终止，可减少页数或稍后重试",
    REASON_CDP: "无法连接 Chrome CDP 调试端口，请先启动专用浏览器并登录",
    REASON_CITY: "城市无法识别，请更换城市名或使用城市代码",
    REASON_UNKNOWN: "抓取失败，详见任务日志",
}

_LOGIN_PATTERNS = (
    re.compile(r"登录查看完整内容"),
    re.compile(r"未登录"),
    re.compile(r"登录态"),
    re.compile(r"请先登录"),
    re.compile(r"login", re.IGNORECASE),
)
_RISK_PATTERNS = (
    re.compile(r"环境存在异常"),
    re.compile(r"访问频繁"),
    re.compile(r"安全校验"),
    re.compile(r"_security_check"),
    re.compile(r"code['\"]?\s*[:=]\s*3[17]"),
    re.compile(r"验证码"),
)
_CDP_PATTERNS = (
    re.compile(r"无法连接\s*CDP", re.IGNORECASE),
    re.compile(r"CDP.*(未就绪|不可用|连接失败)", re.IGNORECASE),
    re.compile(r"WebSocket.*(refused|fail)", re.IGNORECASE),
    re.compile(r"9222"),
)
_CITY_PATTERNS = (re.compile(r"无法识别.*城市"), re.compile(r"城市.*无法识别"))

_PAGE_RE = re.compile(r"第\s*(\d+)\s*页")
_CUMULATIVE_RE = re.compile(r"累计\s*(\d+)")
_PAGE_DONE_RE = re.compile(r"本页\s*(\d+)\s*条")
_DETAIL_RE = re.compile(r"抓取岗位详情\s*\((\d+)\s*个\)")
_DETAIL_ITEM_RE = re.compile(r"^\[(\d+)/(\d+)\]\s")


def classify_failure(log_text: str, return_code: int | None) -> str:
    """根据日志尾部判断失败原因。"""
    if not log_text:
        return REASON_UNKNOWN
    text = strip_ansi(log_text)
    for pattern in _RISK_PATTERNS:
        if pattern.search(text):
            return REASON_RISK
    for pattern in _LOGIN_PATTERNS:
        if pattern.search(text):
            return REASON_LOGIN
    for pattern in _CITY_PATTERNS:
        if pattern.search(text):
            return REASON_CITY
    for pattern in _CDP_PATTERNS:
        if pattern.search(text):
            return REASON_CDP
    if return_code == 0:
        return REASON_UNKNOWN
    return REASON_UNKNOWN


# ------------------------------------------------------------------
# 运行环境
# ------------------------------------------------------------------
@dataclass
class ScraperEnvironment:
    """爬虫运行环境描述。"""

    home_dir: Path
    profile_dir: Path
    python: str
    entry: Path
    cdp_port: int
    env: dict[str, str] = field(default_factory=dict)


def build_environment(profile_hash: str, cdp_port: int | None = None) -> ScraperEnvironment:
    """构造子进程环境变量，把爬虫的「家目录」重定向到工作台 storage。"""
    home_dir = settings.storage_path / "chrome-profiles"
    home_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = settings.profile_dir(profile_hash)
    profile_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    # 关键：爬虫用 os.path.expanduser("~") 推导 profile 与默认结果目录。
    # 覆盖这两个变量即可让它落到工作台自有目录，且不影响宿主机用户配置。
    env["HOME"] = str(home_dir)
    env["USERPROFILE"] = str(home_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    if os.name == "nt":
        # 避免 Windows 控制台默认 GBK 导致的编码崩溃（爬虫内部也会兜底）
        env["PYTHONUTF8"] = "1"

    return ScraperEnvironment(
        home_dir=home_dir,
        profile_dir=profile_dir,
        python=settings.python_executable,
        entry=settings.scraper_entry,
        cdp_port=cdp_port or settings.cdp_port,
        env=env,
    )


def check_scraper_available() -> tuple[bool, str]:
    """检查爬虫脚本和依赖是否就绪。"""
    entry = settings.scraper_entry
    if not entry.is_file():
        return False, f"未找到爬虫主脚本：{entry}（请检查 BZP_SCRAPER_DIR）"
    if not (settings.scraper_path / "data" / "city_codes.json").is_file():
        return False, f"未找到城市码表：{settings.scraper_path / 'data' / 'city_codes.json'}"
    return True, "爬虫脚本就绪"


def read_scraper_version() -> str | None:
    """从爬虫源码里读版本号（仅用于界面展示）。"""
    entry = settings.scraper_entry
    if not entry.is_file():
        return None
    try:
        with entry.open("r", encoding="utf-8", errors="replace") as handle:
            for _ in range(60):
                line = handle.readline()
                if not line:
                    break
                if line.startswith("__version__"):
                    return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        return None
    return None


def cdp_reachable(port: int | None = None, timeout: float = 0.6) -> bool:
    """探测 CDP 端口是否已监听。"""
    target_port = int(port or settings.cdp_port)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            return sock.connect_ex(("127.0.0.1", target_port)) == 0
        except OSError:
            return False


# ------------------------------------------------------------------
# 抓取任务
# ------------------------------------------------------------------
@dataclass
class ScrapeRunResult:
    """一次抓取执行的结果。"""

    ok: bool
    jobs: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, dict[str, Any]] = field(default_factory=dict)
    error_reason: str | None = None
    error_message: str | None = None
    return_code: int | None = None
    duration_seconds: float = 0.0
    log_lines: list[str] = field(default_factory=list)
    jobs_path: Path | None = None
    details_path: Path | None = None
    total_found: int = 0
    new_count: int = 0
    detail_count: int = 0


def _filter_args(task: dict[str, Any]) -> list[str]:
    """把任务字段映射成爬虫的筛选参数。"""
    args: list[str] = []
    mapping = {
        "salary": "--salary",
        "experience": "--experience",
        "degree": "--degree",
        "company_scale": "--scale",
        "company_stage": "--stage",
        "industry": "--industry",
    }
    for field_name, flag in mapping.items():
        value = task.get(field_name)
        if value:
            args.extend([flag, str(value)])
    return args


def _build_command(task: dict[str, Any], output_path: Path, detail_path: Path) -> list[str]:
    env = build_environment(task.get("profile_hash") or "default", task.get("cdp_port"))
    command = [
        env.python,
        str(env.entry),
        "--keyword", str(task["keyword"]),
        "--city", str(task["city"]),
        "--pages", str(int(task.get("max_pages") or 3)),
        "--output", str(output_path),
        "--detail-output", str(detail_path),
        "--cdp-port", str(env.cdp_port),
        "--format", "json",
    ]
    command.extend(_filter_args(task))
    if task.get("with_detail", True):
        command.append("--detail")
    else:
        command.append("--no-detail")
    return command


async def _pump_output(
    stream: asyncio.StreamReader | None,
    on_line: Callable[[str], None] | None,
) -> None:
    """逐行消费子进程**管道**输出。

    仅供外部已有管道句柄的场景使用；工作台自身的抓取链路走
    :func:`run_scrape` 的文件重定向方案（原因见该函数注释）。
    """
    if stream is None:
        return
    while True:
        try:
            raw = await stream.readline()
        except (asyncio.LimitOverrunError, ValueError):
            # 单行超长：丢弃这一行继续，不让日志把任务带崩
            continue
        if not raw:
            break
        line = strip_ansi(raw.decode("utf-8", errors="replace")).rstrip("\r\n")
        if on_line:
            on_line(line)


class _ProgressTracker:
    """把爬虫的 stdout 翻译成任务进度。"""

    def __init__(self, max_pages: int, with_detail: bool) -> None:
        self.max_pages = max(1, int(max_pages))
        self.with_detail = with_detail
        self.current_page = 0
        self.total_found = 0
        self.new_count = 0
        self.detail_total = 0
        self.detail_done = 0

    def feed(self, line: str) -> tuple[int, str] | None:
        """吃一行日志，如果进度有变化就返回 (progress, stage_text)。"""
        page_match = _PAGE_RE.search(line)
        if page_match:
            self.current_page = max(self.current_page, int(page_match.group(1)))

        cumulative = _CUMULATIVE_RE.search(line)
        if cumulative:
            self.total_found = max(self.total_found, int(cumulative.group(1)))

        page_done = _PAGE_DONE_RE.search(line)
        if page_done:
            self.current_page = max(self.current_page, 1)

        detail_match = _DETAIL_RE.search(line)
        if detail_match:
            self.detail_total = int(detail_match.group(1))

        detail_item = _DETAIL_ITEM_RE.match(line.strip())
        if detail_item:
            self.detail_done = int(detail_item.group(1))
            self.detail_total = max(self.detail_total, int(detail_item.group(2)))

        return self._progress()

    def _progress(self) -> tuple[int, str]:
        # 阶段一：列表抓取占 0-70%
        list_ratio = min(1.0, self.current_page / self.max_pages) if self.current_page else 0.0
        list_progress = int(list_ratio * 70)

        if not self.with_detail:
            return min(list_progress, 95), self._list_stage()

        # 阶段二：详情抓取占 70-98%
        if self.detail_total and self.detail_done:
            detail_ratio = min(1.0, self.detail_done / self.detail_total)
            detail_progress = 70 + int(detail_ratio * 28)
            return detail_progress, f"抓取岗位详情 {self.detail_done}/{self.detail_total}"
        if self.detail_total:
            return 72, f"抓取岗位详情 0/{self.detail_total}"
        return min(list_progress, 70), self._list_stage()

    def _list_stage(self) -> str:
        if self.current_page:
            return f"抓取列表 第 {self.current_page}/{self.max_pages} 页"
        return "正在启动抓取"


async def run_scrape(
    task: dict[str, Any],
    task_id: int | str,
    log_path: Path,
    on_progress: ProgressCallback | None = None,
    on_process: Callable[[asyncio.subprocess.Process], None] | None = None,
) -> ScrapeRunResult:
    """执行一次抓取（不含重试）。

    ``task`` 需要的键：keyword / city / max_pages / with_detail / profile_hash /
    cdp_port / salary / experience / degree / company_scale / company_stage / industry。

    ``on_progress`` 在进度变化时被调用（带节流）；``on_process`` 在子进程启动后
    立刻拿到句柄，便于上层实现「取消任务」。
    """
    available, message = check_scraper_available()
    started = time.monotonic()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if not available:
        log_path.write_text(f"{message}\n", encoding="utf-8")
        return ScrapeRunResult(
            ok=False,
            error_reason=REASON_UNKNOWN,
            error_message=message,
            log_lines=[message],
        )

    result_dir = settings.result_dir(task_id)
    result_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    jobs_path = result_dir / f"boss_jobs_{stamp}.json"
    details_path = result_dir / f"boss_details_{stamp}.json"

    env = build_environment(task.get("profile_hash") or "default", task.get("cdp_port"))
    command = _build_command(task, jobs_path, details_path)
    tracker = _ProgressTracker(int(task.get("max_pages") or 3), bool(task.get("with_detail", True)))

    captured: list[str] = []
    last_progress = (-1, "")

    def handle_line(line: str) -> None:
        nonlocal last_progress
        captured.append(line)
        # 只保留尾部，避免超长输出吃内存
        if len(captured) > 4000:
            del captured[:1000]
        update = tracker.feed(line)
        if update and update != last_progress:
            last_progress = update
            if on_progress is not None:
                # 节流：进度写入数据库有成本，只有变化明显时才推
                progress, stage = update
                if progress - last_reported[0] >= 3 or stage != last_reported[1]:
                    last_reported[0] = progress
                    last_reported[1] = stage
                    pending_progress.append(update)

    last_reported: list[Any] = [0, ""]
    pending_progress: list[tuple[int, str]] = []

    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as log_handle:
            log_handle.write(f"$ {' '.join(command)}\n\n")
            log_handle.flush()

            # 用文件而不是管道承接子进程输出，原因有两个：
            # 1. Windows 上 asyncio 的管道走的是命名管道，某些受管控环境
            #    会直接拒绝（WinError 5）；重定向到文件没有这个限制。
            # 2. 抓取任务可能跑几十分钟，文件不会因为缓冲区写满而阻塞子进程。
            # 环境里已设 PYTHONUNBUFFERED=1，输出会及时落盘。
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(settings.scraper_path),
                env=env.env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=log_handle,
                stderr=asyncio.subprocess.STDOUT,
            )
            if on_process is not None:
                on_process(process)

            async def drain_progress() -> None:
                """把待推送的进度交给回调。"""
                while pending_progress:
                    progress, stage = pending_progress.pop(0)
                    if on_progress is not None:
                        await on_progress(progress, stage)

            async def pump_log() -> None:
                """增量读取日志文件，产出进度事件。"""
                try:
                    with log_path.open("r", encoding="utf-8", errors="replace") as reader:
                        buffer = ""
                        while True:
                            chunk = reader.read()
                            if chunk:
                                buffer += chunk
                                while "\n" in buffer:
                                    line, buffer = buffer.split("\n", 1)
                                    handle_line(line.rstrip("\r"))
                            else:
                                # 进程已退出且没有更多内容：冲掉残留并收工
                                if process.returncode is not None:
                                    if buffer.strip():
                                        handle_line(buffer.rstrip("\r"))
                                    return
                                await asyncio.sleep(0.3)
                except OSError:
                    return

            pump_task = asyncio.create_task(pump_log())
            heartbeat = asyncio.create_task(_progress_heartbeat(drain_progress))
            timed_out = False
            try:
                await asyncio.wait_for(process.wait(), timeout=settings.task_timeout_seconds)
            except asyncio.TimeoutError:
                timed_out = True
                with_suppress_terminate(process)
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            finally:
                try:
                    await asyncio.wait_for(pump_task, timeout=5)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pump_task.cancel()
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await heartbeat
                # 收尾时把剩余进度刷出去
                await drain_progress()

        duration = time.monotonic() - started
        log_text = "\n".join(captured[-200:])

        if timed_out:
            return ScrapeRunResult(
                ok=False,
                error_reason=REASON_TIMEOUT,
                error_message=f"抓取超时（>{settings.task_timeout_seconds}s）已终止",
                return_code=process.returncode,
                duration_seconds=duration,
                log_lines=captured[-300:],
                jobs_path=jobs_path,
                details_path=details_path,
            )

        jobs = _load_jobs(jobs_path)
        details = _load_details(details_path)

        if process.returncode != 0 or not jobs:
            reason = classify_failure(log_text, process.returncode)
            # 有时接口正常、只是这一轮没抓到数据，返回码却是 0
            if process.returncode == 0 and not jobs and reason == REASON_UNKNOWN:
                reason = classify_failure(log_text, None)
            return ScrapeRunResult(
                ok=False,
                jobs=jobs,
                details=details,
                error_reason=reason,
                error_message=REASON_TEXT.get(reason, REASON_TEXT[REASON_UNKNOWN]),
                return_code=process.returncode,
                duration_seconds=duration,
                log_lines=captured[-300:],
                jobs_path=jobs_path,
                details_path=details_path,
                total_found=len(jobs),
                detail_count=len(details),
            )

        new_count = sum(1 for job in jobs if job.get("is_new"))
        return ScrapeRunResult(
            ok=True,
            jobs=jobs,
            details=details,
            return_code=process.returncode,
            duration_seconds=duration,
            log_lines=captured[-300:],
            jobs_path=jobs_path,
            details_path=details_path,
            total_found=len(jobs),
            new_count=new_count,
            detail_count=len(details),
        )

    except FileNotFoundError as exc:
        message = f"无法启动爬虫进程：{exc}（请检查 BZP_SCRAPER_PYTHON）"
        _append_log(log_path, message)
        return ScrapeRunResult(
            ok=False,
            error_reason=REASON_UNKNOWN,
            error_message=message,
            duration_seconds=time.monotonic() - started,
            log_lines=[message],
        )
    except OSError as exc:
        message = f"启动爬虫进程失败：{exc}"
        _append_log(log_path, message)
        return ScrapeRunResult(
            ok=False,
            error_reason=REASON_UNKNOWN,
            error_message=message,
            duration_seconds=time.monotonic() - started,
            log_lines=[message],
        )


def with_suppress_terminate(process: asyncio.subprocess.Process) -> None:
    """尽力优雅终止子进程（失败不抛）。"""
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except (ProcessLookupError, OSError):
        return


async def _progress_heartbeat(flush: Callable[[], Awaitable[None]], interval: float = 1.0) -> None:
    """定期把收集到的进度刷给回调，保证前端进度条平滑推进。"""
    while True:
        await asyncio.sleep(interval)
        await flush()


def _spawn_redirected(
    command: list[str],
    *,
    log_path: Path,
    cwd: Path,
    env: dict[str, str],
) -> tuple[asyncio.subprocess.Process, Any]:
    """启动子进程并把输出重定向到文件，返回 ``(process, log_handle)``。

    调用方负责关闭 ``log_handle``（通常用 ``with`` 包住）。

    这里刻意不用 ``stdout=PIPE``：Windows 上 asyncio 的管道基于命名管道，
    部分受管控环境（含 DSH 沙箱的受限模式）会直接拒绝；重定向到文件
    既绕开该限制，也避免长任务把管道缓冲区写满后子进程被阻塞。
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("w", encoding="utf-8", errors="replace")
    process = asyncio.create_subprocess_exec(
        *command,
        cwd=str(cwd),
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=log_handle,
        stderr=asyncio.subprocess.STDOUT,
    )
    return process, log_handle


async def run_capture(
    command: list[str],
    *,
    log_path: Path,
    cwd: Path,
    env: dict[str, str],
    timeout: float = 120.0,
) -> tuple[int | None, str]:
    """执行一个短命令并返回 ``(返回码, 全部输出)``。

    用于 ``--check`` 这类很快结束的调用；输出同样走文件，避免管道限制。
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as handle:
            handle.write(f"$ {' '.join(command)}\n\n")
            handle.flush()
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(cwd),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=handle,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                with_suppress_terminate(process)
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                return process.returncode, ""
    except (OSError, FileNotFoundError) as exc:
        return None, f"启动进程失败：{exc}"

    return process.returncode, log_path.read_text(encoding="utf-8", errors="replace")


def _append_log(log_path: Path, message: str) -> None:
    try:
        with log_path.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(message + "\n")
    except OSError:
        pass


def _load_jobs(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path, default=None)
    if isinstance(payload, dict):
        jobs = payload.get("jobs")
        if isinstance(jobs, list):
            return [item for item in jobs if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _load_details(path: Path) -> dict[str, dict[str, Any]]:
    """详情文件可能是 list，也可能是带 details 键的 dict。"""
    payload = read_json(path, default=None)
    items: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        raw = payload.get("details") or payload.get("jobs")
        if isinstance(raw, list):
            items = [item for item in raw if isinstance(item, dict)]
    elif isinstance(payload, list):
        items = [item for item in payload if isinstance(item, dict)]

    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        key = str(item.get("job_id") or item.get("job_link") or item.get("link") or "")
        if key:
            indexed[key] = item
    return indexed


# ------------------------------------------------------------------
# 结果 -> 数据库记录
# ------------------------------------------------------------------
def normalize_job(
    job: dict[str, Any], detail: dict[str, Any] | None = None
) -> dict[str, Any]:
    """把「列表条目 + 详情条目」归一化成 ``JobRecord`` 的字段字典。"""
    detail = detail or {}

    title = str(job.get("title") or detail.get("title") or "").strip()
    company = str(job.get("boss_name") or detail.get("company") or "").strip()
    salary_text = str(job.get("salary") or detail.get("salary") or "").strip() or None
    parsed_salary = parse_salary(salary_text)

    location = str(job.get("location") or detail.get("location") or "").strip() or None
    city = location.split("·")[0] if location else None

    experience = normalize_experience(job.get("experience"))
    degree = normalize_degree(job.get("degree"))
    if not experience or not degree:
        tag_exp, tag_degree = split_tags(job.get("tags"))
        experience = experience or tag_exp
        degree = degree or tag_degree

    jd = str(detail.get("jd") or "").strip() or None
    duty, requirement = split_jd(jd)

    raw_skills = [item.strip() for item in str(job.get("skills") or "").split("|") if item.strip()]
    detail_tags = detail.get("skill_tags")
    if isinstance(detail_tags, list):
        raw_skills.extend(str(item).strip() for item in detail_tags if str(item).strip())
    labels = [item.strip() for item in str(job.get("job_labels") or "").split("|") if item.strip()]
    raw_skills.extend(labels)

    # 去重同时保序
    seen: set[str] = set()
    deduped_skills: list[str] = []
    for skill in raw_skills:
        lowered = skill.lower()
        if lowered and lowered not in seen:
            seen.add(lowered)
            deduped_skills.append(skill)

    skill_keywords = extract_skill_keywords(jd, extra=deduped_skills, limit=24)

    job_link = str(job.get("job_link") or detail.get("job_link") or detail.get("link") or "").strip() or None

    job_id = str(job.get("job_id") or detail.get("job_id") or "").strip()
    if not job_id:
        # 理论上爬虫一定会给 job_id，这里兜底避免唯一约束炸掉整批入库
        from ..utils import fingerprint

        job_id = fingerprint(job_link or f"{title}|{company}")

    return {
        "job_id": job_id,
        "title": title or "未命名岗位",
        "company": company or "未知公司",
        "salary": salary_text,
        "salary_source": job.get("salary_source") or detail.get("salary_source"),
        "salary_min": parsed_salary.low,
        "salary_max": parsed_salary.high,
        "salary_avg": parsed_salary.avg,
        "city": city,
        "location": location,
        "experience": experience,
        "degree": degree,
        "company_scale": job.get("company_scale") or None,
        "company_stage": job.get("company_stage") or None,
        "company_industry": job.get("company_industry") or None,
        "boss_name": job.get("boss_name") or None,
        "boss_title": job.get("boss_title") or None,
        "boss_active_status": job.get("boss_active_status") or detail.get("boss_active_status") or None,
        "tags": job.get("tags") or None,
        "skills_raw": job.get("skills") or None,
        "welfare": job.get("welfare") or None,
        "skill_tags": _dump_list(deduped_skills),
        "skill_keywords": _dump_list(skill_keywords),
        "jd": jd,
        "job_duty": duty,
        "job_requirement": requirement,
        "job_link": job_link,
        "company_link": job.get("company_link") or None,
        "security_id": job.get("security_id") or None,
        "lid": job.get("lid") or None,
        "is_new": bool(job.get("is_new", True)),
        "detail_fetched": bool(jd),
        "raw_payload": _dump_payload(job, detail),
    }


def _dump_list(items: list[str]) -> str | None:
    import json

    if not items:
        return None
    return json.dumps(items, ensure_ascii=False)


def _dump_payload(job: dict[str, Any], detail: dict[str, Any] | None) -> str | None:
    """把原始条目序列化保存，便于后续回溯爬虫字段变化。"""
    import json

    # 避免把超大字段（例如超长 JD）整段存两遍
    trimmed_detail = dict(detail or {})
    if isinstance(trimmed_detail.get("jd"), str) and len(trimmed_detail["jd"]) > 20000:
        trimmed_detail["jd"] = trimmed_detail["jd"][:20000] + "…（已截断）"

    try:
        return json.dumps({"job": job, "detail": trimmed_detail}, ensure_ascii=False)[:60000]
    except (TypeError, ValueError):
        # 兜底：至少保证是一条合法 JSON，方便前端与导出解析
        return json.dumps({"job": {}, "detail": {}}, ensure_ascii=False)


def merge_jobs_with_details(
    jobs: list[dict[str, Any]], details: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """按 job_id / job_link 把详情挂到列表条目上。"""
    merged: list[dict[str, Any]] = []
    for job in jobs:
        key = str(job.get("job_id") or "")
        link = str(job.get("job_link") or "")
        detail = details.get(key) or details.get(link) or {}
        merged.append(normalize_job(job, detail))
    return merged


def read_task_log(task_id: int | str, limit: int = 300) -> tuple[list[str], int]:
    """读取任务日志尾部。"""
    from .logging_service import task_log_path

    path = task_log_path(task_id)
    x, total = tail_lines(path, limit=limit)
    return x, total


def resolve_scraper_python() -> str:
    """返回用于执行爬虫的解释器；不存在时回退当前解释器。"""
    candidate = settings.scraper_python
    if candidate and Path(candidate).is_file():
        return candidate
    if candidate and shutil.which(candidate):
        return candidate
    return sys.executable


def utc_now_iso() -> str:
    return utcnow().isoformat()
