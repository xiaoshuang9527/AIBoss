"""账号授权：扫码登录会话管理、登录态校验与持久化。

设计权衡
--------
BOSS 直聘的 CDP 登录必须在**有界面**的浏览器里人工扫码，无法在纯无头
服务器上完成。因此：

* 授权接口启动一个真实的浏览器进程（服务端需可显示桌面 / X11 转发）；
* 进程输出实时回传前端，用户扫码完成后由后台任务自动抓取登录态快照；
* 纯无头部署时接口会明确返回提示，并支持 ``copy_login_state``
  从已登录的浏览器导入 Cookie（需用户知情）。

会话是**全局单例**的：同时发起两个扫码流程只会让 Chrome profile 互踩。
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..enums import AuthStatus, LogCategory, LogLevel
from ..models import AuthProfile
from ..utils import fingerprint, strip_ansi, to_naive_utc, utcnow
from . import crypto_store
from .logging_service import log_event
from .scraper_runner import (
    build_environment,
    cdp_reachable,
    check_scraper_available,
    run_capture,
)

LOGIN_URL = "https://www.zhipin.com/web/user/?ka=header-login"

_PHASE_LABELS = {
    "starting": "正在启动浏览器",
    "awaiting_login": "等待扫码登录",
    "verifying": "正在校验登录态",
    "success": "授权成功",
    "failed": "授权失败",
    "cancelled": "已取消",
}

# 登录成功的判据：爬虫 --setup-chrome 在确认接口能返回明文薪资后才算完成
_SUCCESS_PATTERNS = (
    re.compile(r"登录态.*(正常|有效|已保存)"),
    re.compile(r"已登录"),
    re.compile(r"登录成功"),
    re.compile(r"setup.*(完成|成功)", re.IGNORECASE),
    re.compile(r"明文薪资.*(确认|校验)"),
)
_FAILURE_PATTERNS = (
    re.compile(r"环境存在异常"),
    re.compile(r"访问频繁"),
    re.compile(r"登录超时"),
    re.compile(r"未完成登录"),
)


@dataclass
class LoginSession:
    """一次扫码授权会话。"""

    session_id: str
    profile_id: int
    profile_hash: str
    browser: str
    cdp_port: int
    copy_login_state: bool
    reset_profile: bool
    external_browser: bool = False
    phase: str = "starting"
    message: str = "正在启动专用浏览器…"
    login_url: str | None = None
    qr_hint: str | None = None
    started_at: Any = field(default_factory=utcnow)
    finished_at: Any = None
    lines: list[str] = field(default_factory=list)
    process: asyncio.subprocess.Process | None = None
    task: asyncio.Task | None = None
    error: str | None = None
    log_path: str | None = None

    def to_payload(self, tail: int = 60) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "profile_id": self.profile_id,
            "browser": self.browser,
            "cdp_port": self.cdp_port,
            "phase": self.phase,
            "phase_label": _PHASE_LABELS.get(self.phase, self.phase),
            "message": self.message,
            "login_url": self.login_url,
            "qr_hint": self.qr_hint,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "log_tail": self.lines[-tail:],
        }


class LoginSessionManager:
    """全局唯一的授权会话管理器。"""

    def __init__(self) -> None:
        self._session: LoginSession | None = None
        self._lock = asyncio.Lock()
        self._listeners: list[asyncio.Queue] = []
        self._log_handle: Any = None

    # ---------------- 订阅（给 WebSocket 用） ----------------
    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._listeners.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self._listeners:
            self._listeners.remove(queue)

    def _broadcast(self) -> None:
        if self._session is None:
            return
        payload = self._session.to_payload()
        for queue in list(self._listeners):
            if queue.full():
                continue
            try:
                queue.put_nowait({"type": "auth.progress", "data": payload})
            except asyncio.QueueFull:
                continue

    # ---------------- 查询 ----------------
    @property
    def current(self) -> LoginSession | None:
        return self._session

    def is_busy(self) -> bool:
        return self._session is not None and self._session.phase in {
            "starting",
            "awaiting_login",
            "verifying",
        }

    # ---------------- 启动 ----------------
    async def start(
        self,
        db: AsyncSession,
        *,
        browser: str = "chrome",
        copy_login_state: bool = False,
        reset_profile: bool = False,
        name: str = "默认账号",
        external_browser: bool = False,
    ) -> LoginSession:
        async with self._lock:
            if self.is_busy():
                assert self._session is not None
                return self._session

            available, message = check_scraper_available()
            if not available:
                raise RuntimeError(message)

            profile = await ensure_default_profile(db, browser=browser, name=name)

            session = LoginSession(
                session_id=uuid.uuid4().hex[:16],
                profile_id=profile.id,
                profile_hash=profile.profile_hash,
                browser=browser,
                cdp_port=profile.cdp_port,
                copy_login_state=copy_login_state,
                reset_profile=reset_profile,
                external_browser=external_browser,
            )
            session.login_url = LOGIN_URL
            if external_browser:
                session.qr_hint = (
                    f"已切换为「接管现有浏览器」模式：请确认已用隔离 profile 启动 Chrome 并打开 "
                    f"zhipin.com，且 CDP 端口 {profile.cdp_port} 已监听。登录成功后会自动保存登录态。"
                )
            else:
                session.qr_hint = (
                    "专用浏览器窗口已打开。请在窗口内用 BOSS 直聘 App 扫码登录；"
                    "登录成功后无需手动操作，工作台会自动保存登录态。"
                )
            self._session = session

            profile.status = AuthStatus.AUTHORIZING.value
            profile.browser = browser
            profile.last_error = None
            await db.commit()

            await log_event(
                db,
                f"启动 BOSS 直聘授权会话（浏览器={browser}"
                f"{'，接管现有实例' if external_browser else ''}）",
                category=LogCategory.AUTH,
                auth_profile_id=profile.id,
            )

            session.task = asyncio.create_task(self._run(session, profile.id))
            return session

    async def _run(self, session: LoginSession, profile_id: int) -> None:
        """在后台跑 ``--setup-chrome`` 并吸收它的输出。

        ``external_browser=True`` 时跳过启动步骤，直接接管已开启 CDP 端口的实例——
        这是服务端无法拉起 Chrome（无图形界面 / 运行环境受限）时的唯一可用路径。
        """
        env = build_environment(session.profile_hash, session.cdp_port)

        if session.external_browser:
            session.phase = "awaiting_login"
            session.message = session.qr_hint or "等待已启动的浏览器完成登录"
            self._broadcast()
            await self._await_login(session, profile_id)
            return

        command = [
            env.python,
            str(env.entry),
            "--setup-chrome" if session.browser == "chrome" else "--setup-edge",
            "--cdp-port", str(session.cdp_port),
            "--login-timeout", str(settings.auth_timeout_seconds),
            "--no-wait-login",
        ]
        if session.copy_login_state:
            command.append("--copy-login-state")
        if session.reset_profile:
            command.append("--reset-chrome-profile")

        session.lines.append(f"$ {' '.join(command)}")
        session.phase = "awaiting_login"
        session.message = session.qr_hint or "等待扫码登录"
        self._broadcast()

        log_path = settings.storage_path / "logs" / f"auth-{session.session_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # 输出重定向到文件（Windows/受限环境下管道不可用），再轮询读取
        session.log_path = str(log_path)
        try:
            self._log_handle = log_path.open("w", encoding="utf-8", errors="replace")
            self._log_handle.write(f"$ {' '.join(command)}\n\n")
            self._log_handle.flush()
            session.process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(settings.scraper_path),
                env=env.env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=self._log_handle,
                stderr=asyncio.subprocess.STDOUT,
            )
        except (OSError, FileNotFoundError) as exc:
            await self._close_log_handle()
            await self._finish(session, profile_id, "failed", f"无法启动浏览器进程：{exc}")
            return

        async def relay() -> None:
            """增量读取日志文件并广播给前端。"""
            try:
                with log_path.open("r", encoding="utf-8", errors="replace") as reader:
                    buffer = ""
                    while True:
                        chunk = reader.read()
                        if chunk:
                            buffer += chunk
                            while "\n" in buffer:
                                line, buffer = buffer.split("\n", 1)
                                line = line.rstrip("\r")
                                if not line:
                                    continue
                                session.lines.append(line)
                                if len(session.lines) > 800:
                                    del session.lines[:200]
                                self._broadcast()
                        elif session.process is not None and session.process.returncode is not None:
                            return
                        else:
                            await asyncio.sleep(0.5)
            except OSError:
                return

        relay_task = asyncio.create_task(relay())

        # 等待浏览器起来 + 用户扫码。--no-wait-login 让进程立刻返回，
        # 所以我们自己按「CDP 端口出现 + 轮询登录态」来判断完成。
        await self._await_login(session, profile_id)

        try:
            await asyncio.wait_for(relay_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            relay_task.cancel()
        await self._close_log_handle()

    async def _close_log_handle(self) -> None:
        handle = getattr(self, "_log_handle", None)
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
            self._log_handle = None

    async def _await_login(self, session: LoginSession, profile_id: int) -> None:
        """轮询 CDP + 登录态，直到成功或超时。"""
        deadline = time.monotonic() + settings.auth_timeout_seconds
        saw_cdp = False
        grace = 0

        while time.monotonic() < deadline:
            if cdp_reachable(session.cdp_port):
                if not saw_cdp:
                    saw_cdp = True
                    session.message = "浏览器已就绪，等待扫码登录…"
                    self._broadcast()
                # 关键：必须用**本次会话自己的** profile 去校验，
                # 否则 --check 会在另一个 HOME 下找浏览器，永远判定未登录。
                outcome = await check_login_state(session.cdp_port, session.profile_hash)

                if outcome["ok"]:
                    session.phase = "verifying"
                    session.message = "检测到登录态，正在保存…"
                    self._broadcast()
                    await self._finish(session, profile_id, "success", "授权成功，登录态已保存")
                    return

                if outcome.get("fatal"):
                    await self._finish(
                        session, profile_id, "failed", outcome.get("detail") or "触发风控校验"
                    )
                    return

                grace = 0
            else:
                grace += 1
                if saw_cdp and grace > 12:
                    # CDP 端口消失说明浏览器被关掉了
                    await self._finish(session, profile_id, "failed", "专用浏览器已关闭，授权中断")
                    return

            # 从日志里捞失败信号
            joined = "\n".join(session.lines[-40:])
            for pattern in _FAILURE_PATTERNS:
                if pattern.search(joined):
                    await self._finish(
                        session, profile_id, "failed", f"登录过程被中断：{pattern.pattern}"
                    )
                    return
            await asyncio.sleep(3)

        await self._finish(session, profile_id, "failed", "等待登录超时，请重新发起授权")

    async def _finish(
        self, session: LoginSession, profile_id: int, phase: str, message: str
    ) -> None:
        """收尾：更新会话状态、落库、抓取登录态快照。"""
        from ..db import SessionLocal

        session.phase = phase
        session.message = message
        session.finished_at = utcnow()
        if phase != "success":
            session.error = message

        async with SessionLocal() as db:
            profile = await db.get(AuthProfile, profile_id)
            if profile is not None:
                if phase == "success":
                    profile_dir = settings.profile_dir(session.profile_hash)
                    snapshot_path, cookie_fp = crypto_store.capture_cookies(profile_dir)
                    profile.status = AuthStatus.ACTIVE.value
                    profile.last_verified_at = utcnow()
                    profile.last_error = None
                    if snapshot_path is not None:
                        profile.cookie_blob_path = str(snapshot_path)
                        profile.cookie_fingerprint = cookie_fp
                    profile.account_label = profile.account_label or "已授权账号"
                elif phase == "cancelled":
                    profile.status = AuthStatus.UNKNOWN.value
                else:
                    profile.status = AuthStatus.EXPIRED.value
                    profile.last_error = message
                profile.updated_at = utcnow()
                await db.commit()

                await log_event(
                    db,
                    f"授权会话结束：{message}",
                    level=LogLevel.INFO if phase == "success" else LogLevel.WARNING,
                    category=LogCategory.AUTH,
                    auth_profile_id=profile_id,
                )
        self._broadcast()

    async def cancel(self) -> bool:
        """取消当前授权会话。"""
        session = self._session
        if session is None or not self.is_busy():
            return False
        if session.process and session.process.returncode is None:
            try:
                session.process.terminate()
            except (ProcessLookupError, OSError):
                pass
        if session.task:
            session.task.cancel()
        await self._finish(session, session.profile_id, "cancelled", "已取消授权")
        self._session = None
        return True


login_manager = LoginSessionManager()


# ============================================================
# 登录态校验
# ============================================================
# ---- 判定关键词 ----
# 注意：「✅ 通过 — CDP 服务: Chrome/...」只代表 CDP 端口通了，
# 不代表已登录，所以这些词只能作为「非决定性」的辅助信号。
_CHECK_OK = (
    re.compile(r"登录态.*(正常|有效)"),
    re.compile(r"已登录"),
    re.compile(r"所有检查通过"),
    re.compile(r"环境检查.*通过(?!.*未通过)"),
)

# 明确的未登录信号（爬虫 --check 第 3 项失败时打印这些）
_CHECK_LOGIN_FAIL = (
    re.compile(r"未检测到可用登录态"),
    re.compile(r"部分检查未通过"),
    re.compile(r"未登录"),
    re.compile(r"登录态.*(失效|无效|过期)"),
    re.compile(r"请先登录"),
    re.compile(r"登录查看完整内容"),
)

# 风控信号
_CHECK_RISK = (
    re.compile(r"环境存在异常"),
    re.compile(r"访问频繁"),
    re.compile(r"安全校验"),
    re.compile(r"_security_check"),
)

# 判定 BOSS 直聘登录态的关键 Cookie。
# zhipin.com 在用户登录后会下发这两类会话票据之一。
_ZHIPIN_SESSION_COOKIES = ("bst", "geek_zp_token", "zp_at", "atoken", "wt2", "zp_token")


def _iter_cookie_db_files(chrome_profile_dir: Path) -> list[Path]:
    """枚举 Chrome profile 里所有 Cookies 数据库文件。"""
    if not chrome_profile_dir.is_dir():
        return []
    candidates: list[Path] = []
    # 新版 Chrome 把 Cookie 放在 Default/Network 下，老版在 Default 下
    for relative in ("Default/Network/Cookies", "Default/Cookies", "Network/Cookies", "Cookies"):
        candidate = chrome_profile_dir / relative
        if candidate.is_file():
            candidates.append(candidate)
    for pattern in ("*/Network/Cookies", "*/Cookies"):
        for found in chrome_profile_dir.glob(pattern):
            if found.is_file() and found not in candidates:
                candidates.append(found)
    return candidates


def _cookie_db_has_zhipin_session(cookie_file: Path) -> tuple[bool, int]:
    """只读扫描 Cookie 数据库，判断是否存在 zhipin.com 的登录票据。

    返回 ``(是否已登录, zhipin 域 Cookie 数量)``。

    为什么要单独做这件事：``--check`` 依赖爬虫自身的判定逻辑，一旦它的
    输出格式变化或它在错误 profile 下运行，工作台就会误判。直接读 Cookie
    数据库是一个**独立**的证据来源，两者取并集能显著降低误判概率。

    实现要点：Chrome 运行时会对该库加写锁并留下 ``-journal`` 文件，
    此时直接以 ``mode=ro`` 打开会报 ``unable to open database file``。
    所以先把库（连同 journal）复制到临时目录再读——只读复制不影响 Chrome。
    """
    import shutil
    import sqlite3
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "Cookies"
        try:
            shutil.copy2(cookie_file, target)
            # 有 journal 说明事务未提交，一起带过去 SQLite 才能回放出完整数据
            for suffix in ("-journal", "-wal", "-shm"):
                sidecar = Path(str(cookie_file) + suffix)
                if sidecar.is_file():
                    shutil.copy2(sidecar, Path(str(target) + suffix))
        except OSError:
            return False, 0

        try:
            connection = sqlite3.connect(str(target), timeout=5.0)
        except sqlite3.Error:
            return False, 0

        try:
            cursor = connection.execute(
                "SELECT host_key, name FROM cookies WHERE host_key LIKE '%zhipin.com%'"
            )
            rows = cursor.fetchall()
        except sqlite3.Error:
            return False, 0
        finally:
            connection.close()

    names = {str(name).lower() for _host, name in rows}
    logged_in = any(cookie.lower() in names for cookie in _ZHIPIN_SESSION_COOKIES)
    return logged_in, len(rows)


def detect_login_from_profile(profile_dir: Path) -> dict[str, Any]:
    """直接读隔离 profile 的 Cookie 数据库来判断登录态。"""
    files = _iter_cookie_db_files(profile_dir)
    if not files:
        return {"ok": False, "detail": "尚未生成 Cookie 数据库（浏览器还没写入）", "cookies": 0}

    total = 0
    for cookie_file in files:
        logged_in, count = _cookie_db_has_zhipin_session(cookie_file)
        total += count
        if logged_in:
            return {
                "ok": True,
                "detail": f"检测到 BOSS 直聘登录票据（{count} 条 zhipin Cookie）",
                "cookies": count,
            }
    return {
        "ok": False,
        "detail": f"没有找到登录票据（zhipin Cookie {total} 条，可能尚未登录）",
        "cookies": total,
    }


async def run_check(cdp_port: int, profile_hash: str | None = None, timeout: int = 90) -> dict[str, Any]:
    """执行 ``--check`` 环境检查，返回结构化结果。

    ``profile_hash`` 必须传入**当前使用的**授权档案，否则子进程会在错误的
    ``HOME`` 下查找浏览器 profile，把「已登录」误判成「未启动」。
    """
    available, message = check_scraper_available()
    if not available:
        return {"ok": False, "detail": message, "fatal": False, "raw": ""}

    prof = profile_hash or await _any_profile_hash()
    env = build_environment(prof, cdp_port)
    command = [
        env.python,
        str(env.entry),
        "--check",
        "--cdp-port", str(cdp_port),
    ]

    log_path = settings.storage_path / "logs" / f"check-{cdp_port}.log"
    return_code, text = await run_capture(
        command,
        log_path=log_path,
        cwd=settings.scraper_path,
        env=env.env,
        timeout=timeout,
    )

    if return_code is None:
        return {"ok": False, "detail": text or "无法执行环境检查", "fatal": False, "raw": ""}
    if text == "":
        return {"ok": False, "detail": "环境检查超时", "fatal": False, "raw": ""}

    # ---- 判定顺序很关键 ----
    # 爬虫的 --check 是「依赖 / CDP 端口 / 登录态」三项全过才返回 0，
    # 任一失败返回 1。而它的成功分支也会打印「✅ 通过 — CDP 服务: ...」，
    # 所以**绝不能先看返回码再匹配文本**，否则 CDP 通了但没登录时会被
    # 误判成「登录态有效」。必须先找失败信号。
    for pattern in _CHECK_RISK:
        if pattern.search(text):
            return {"ok": False, "detail": "触发 BOSS 直聘风控校验", "fatal": True, "raw": text}

    for pattern in _CHECK_LOGIN_FAIL:
        if pattern.search(text):
            return {"ok": False, "detail": "登录态已失效，请重新登录", "fatal": False, "raw": text}

    if return_code == 0:
        return {"ok": True, "detail": "登录态有效", "fatal": False, "raw": text}

    for pattern in _CHECK_OK:
        if pattern.search(text):
            # 返回码非 0 但明确报告登录态正常：可能是 CDP/依赖之外的子项告警，
            # 以文本为准（这条路径很罕见，保留以免漏判）。
            return {"ok": True, "detail": "登录态有效", "fatal": False, "raw": text}

    return {
        "ok": False,
        "detail": "环境检查未通过（浏览器未启动或尚未登录）",
        "fatal": False,
        "raw": text,
    }


async def _any_profile_hash() -> str:
    """取一个已存在的 profile 名用于构造环境（校验阶段不关心具体档案）。"""
    from ..db import SessionLocal

    async with SessionLocal() as db:
        result = await db.execute(select(AuthProfile).limit(1))
        profile = result.scalar_one_or_none()
        if profile is not None:
            return profile.profile_hash
    return "default"


async def check_login_state(
    cdp_port: int, profile_hash: str | None = None
) -> dict[str, Any]:
    """综合判断登录态：CDP 端口 → 爬虫 ``--check`` → Cookie 数据库兜底。

    两条证据链取并集：
    * 爬虫的 ``--check`` 会真正调一次搜索接口，最权威，但依赖它的输出格式；
    * 直接读 Cookie 数据库不依赖爬虫，能兜住「接口偶发失败但登录态确实有效」。
    """
    if not cdp_reachable(cdp_port):
        return {"ok": False, "detail": "CDP 端口未监听", "fatal": False}

    outcome = await run_check(cdp_port, profile_hash)
    if outcome.get("ok") or outcome.get("fatal"):
        return outcome

    # --check 不通过时，用 Cookie 数据库再确认一次。
    # 专用浏览器真实的 profile 落在 HOME/.boss-zhipin-scraper/chrome-profile，
    # 不在档案自己的目录里，两处都查一遍。
    if profile_hash:
        profile_root = settings.storage_path / "chrome-profiles" / profile_hash
        for candidate in (
            profile_root / ".boss-zhipin-scraper" / "chrome-profile",
            profile_root,
        ):
            cookie_result = detect_login_from_profile(candidate)
            if cookie_result["ok"]:
                return {
                    "ok": True,
                    "detail": cookie_result["detail"],
                    "fatal": False,
                    "raw": outcome.get("raw", ""),
                }
    return outcome


# ============================================================
# 档案管理
# ============================================================
def default_profile_hash() -> str:
    return fingerprint("boss-zhipin", "default")


async def ensure_default_profile(
    db: AsyncSession, *, browser: str = "chrome", name: str = "默认账号"
) -> AuthProfile:
    """获取（必要时创建）默认授权档案。"""
    profile_hash = default_profile_hash()
    result = await db.execute(select(AuthProfile).where(AuthProfile.profile_hash == profile_hash))
    profile = result.scalar_one_or_none()
    if profile is None:
        profile = AuthProfile(
            name=name,
            profile_dir=str(settings.profile_dir(profile_hash)),
            profile_hash=profile_hash,
            browser=browser,
            cdp_port=settings.cdp_port,
            status=AuthStatus.UNKNOWN.value,
            is_default=True,
        )
        db.add(profile)
        await db.commit()
        await db.refresh(profile)
    return profile


async def get_active_profile(db: AsyncSession) -> AuthProfile | None:
    """返回当前应使用的档案：优先 active，其次默认档案。"""
    result = await db.execute(
        select(AuthProfile).where(AuthProfile.status == AuthStatus.ACTIVE.value).limit(1)
    )
    profile = result.scalar_one_or_none()
    if profile is not None:
        return profile
    result = await db.execute(select(AuthProfile).order_by(AuthProfile.is_default.desc()).limit(1))
    return result.scalar_one_or_none()


async def get_default_or_create(db: AsyncSession) -> AuthProfile:
    profile = await get_active_profile(db)
    if profile is None:
        profile = await ensure_default_profile(db)
    return profile


async def refresh_profile_status(db: AsyncSession, profile: AuthProfile) -> AuthProfile:
    """校验并刷新档案状态（供「账号授权」页刷新按钮调用）。"""
    if profile.status == AuthStatus.AUTHORIZING.value and login_manager.is_busy():
        return profile

    outcome = await check_login_state(profile.cdp_port, profile.profile_hash)
    profile.last_verified_at = utcnow()
    if outcome["ok"]:
        profile.status = AuthStatus.ACTIVE.value
        profile.last_error = None
        profile_dir = Path(profile.profile_dir)
        if profile_dir.is_dir():
            snapshot_path, cookie_fp = crypto_store.capture_cookies(profile_dir)
            if snapshot_path is not None:
                profile.cookie_blob_path = str(snapshot_path)
                profile.cookie_fingerprint = cookie_fp
    else:
        profile.status = AuthStatus.EXPIRED.value
        profile.last_error = str(outcome.get("detail") or "登录态校验未通过")
    profile.updated_at = utcnow()
    await db.commit()
    await db.refresh(profile)
    await log_event(
        db,
        f"刷新授权状态：{profile.status}",
        level=LogLevel.INFO if outcome["ok"] else LogLevel.WARNING,
        category=LogCategory.AUTH,
        auth_profile_id=profile.id,
        detail=str(outcome.get("detail") or ""),
        commit=True,
    )
    return profile


def profile_status_label(status: str) -> str:
    try:
        return AuthStatus(status).label
    except ValueError:
        return status


def is_profile_fresh(profile: AuthProfile, max_age_seconds: int = 1800) -> bool:
    """登录态是否在有效期内（避免每次任务都跑一次环境检查）。"""
    verified = to_naive_utc(profile.last_verified_at)
    if verified is None:
        return False
    return (utcnow().replace(tzinfo=None) - verified) < timedelta(seconds=max_age_seconds)


__all__ = [
    "LOGIN_URL",
    "check_login_state",
    "detect_login_from_profile",
    "ensure_default_profile",
    "get_active_profile",
    "get_default_or_create",
    "is_profile_fresh",
    "login_manager",
    "profile_status_label",
    "refresh_profile_status",
    "run_check",
]
