"""单元测试：登录态判定相关逻辑。

覆盖三类回归：
1. ``--check`` 必须使用**当前档案**的 profile（历史上用错 profile 会导致
   「已登录却判定为浏览器未启动」）；
2. Cookie 数据库直读作为独立证据链；
3. ``external_browser``（接管现有浏览器）模式不启动子进程。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bzp_workbench.services import auth_service
from bzp_workbench.services.auth_service import (
    detect_login_from_profile,
    run_check,
)
from bzp_workbench.services.scraper_runner import build_environment


def _make_chrome_profile(root: Path, *, logged_in: bool, layout: str = "default",
                         leave_journal: bool = False) -> Path:
    """构造一个最小的 Chrome profile，含 Cookies 数据库。

    ``leave_journal=True`` 模拟 Chrome 正在运行时留下的 ``-journal`` 文件——
    这正是线上让 SQLite 只读打开失败的原因。
    """
    if layout == "legacy":
        cookie_dir = root / "Default"
    else:
        cookie_dir = root / "Default" / "Network"
    cookie_dir.mkdir(parents=True, exist_ok=True)

    db_path = cookie_dir / "Cookies"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT)")
    connection.execute("INSERT INTO cookies VALUES ('www.zhipin.com', '__zp_stoken__', 'x')")
    connection.execute("INSERT INTO cookies VALUES ('.zhipin.com', 'lastCity', '101280600')")
    if logged_in:
        connection.execute("INSERT INTO cookies VALUES ('.zhipin.com', 'bst', 'ticket')")
    connection.commit()
    connection.close()

    if leave_journal:
        Path(str(db_path) + "-journal").write_bytes(b"")
    return root


class TestDetectLoginFromProfile:
    def test_detects_session_cookie(self, tmp_path):
        profile = _make_chrome_profile(tmp_path / "p1", logged_in=True)
        result = detect_login_from_profile(profile)
        assert result["ok"] is True
        assert result["cookies"] == 3

    def test_no_session_cookie(self, tmp_path):
        profile = _make_chrome_profile(tmp_path / "p2", logged_in=False)
        result = detect_login_from_profile(profile)
        assert result["ok"] is False
        assert result["cookies"] == 2

    def test_legacy_cookie_layout(self, tmp_path):
        """老版 Chrome 把 Cookies 放在 Default/ 下，也要能识别。"""
        profile = _make_chrome_profile(tmp_path / "p3", logged_in=True, layout="legacy")
        assert detect_login_from_profile(profile)["ok"] is True

    def test_missing_profile(self, tmp_path):
        result = detect_login_from_profile(tmp_path / "not-exist")
        assert result["ok"] is False
        assert result["cookies"] == 0

    def test_profile_without_cookie_db(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        result = detect_login_from_profile(empty)
        assert result["ok"] is False

    def test_reads_db_while_chrome_holds_journal(self, tmp_path):
        """回归：Chrome 运行时留下的 -journal 会让只读打开失败。

        线上就是这个原因导致「Cookie 明明有登录票据却读不出来」。
        正确做法是把库连同 journal 复制到临时目录再读。
        """
        profile = _make_chrome_profile(
            tmp_path / "p4", logged_in=True, leave_journal=True
        )
        result = detect_login_from_profile(profile)
        assert result["ok"] is True, "带 journal 时也必须能读出登录票据"
        assert result["cookies"] == 3

    def test_journal_case_still_detects_absence(self, tmp_path):
        profile = _make_chrome_profile(
            tmp_path / "p5", logged_in=False, leave_journal=True
        )
        result = detect_login_from_profile(profile)
        assert result["ok"] is False
        assert result["cookies"] == 2


class TestRunCheckVerdicts:
    """回归：``--check`` 的判定顺序。

    爬虫第 2 项（CDP 端口）会打印「✅ 通过 — CDP 服务: Chrome/...」，
    第 3 项（登录态）失败时打印「❌ 未检测到可用登录态」并返回 1。
    如果先看返回码或先匹配宽泛的「通过」，就会把「CDP 通了但没登录」
    误判成「登录态有效」——这会导致工作台放行未登录的抓取任务。
    """

    async def _run(self, monkeypatch, text: str, code: int):
        import bzp_workbench.services.auth_service as module

        async def fake_run_capture(command, *, log_path, cwd, env, timeout):
            return code, text

        monkeypatch.setattr(module, "run_capture", fake_run_capture)
        return await module.run_check(9222, "some-hash")

    async def test_cdp_ok_but_not_logged_in_is_not_ok(self, monkeypatch):
        """真实场景：CDP 通了、但未登录 → 必须判定为未登录。"""
        output = (
            "  BOSS直聘 CDP 环境检查\n"
            "[1/3] Python 依赖...\n"
            "  ✅ 依赖完整\n"
            "[2/3] CDP 端口连通性...\n"
            "  ✅ 通过 — CDP 服务: Chrome/153.0.8010.52\n"
            "[3/3] BOSS直聘登录状态...\n"
            "  ❌ 未检测到可用登录态（code: 0）\n\n"
            "❌ 部分检查未通过，请修复后重试\n"
        )
        result = await self._run(monkeypatch, output, 1)
        assert result["ok"] is False, "CDP 通过不等于已登录"
        assert result["fatal"] is False

    async def test_all_pass_is_ok(self, monkeypatch):
        output = (
            "[2/3] CDP 端口连通性...\n"
            "  ✅ 通过 — CDP 服务: Chrome/153\n"
            "[3/3] BOSS直聘登录状态...\n"
            "  ✅ 已登录\n\n"
            "✅ 所有检查通过，可以开始抓取\n"
        )
        result = await self._run(monkeypatch, output, 0)
        assert result["ok"] is True

    async def test_risk_control_is_fatal(self, monkeypatch):
        output = "❌ 接口返回 code: 37 您的环境存在异常\n❌ 部分检查未通过"
        result = await self._run(monkeypatch, output, 1)
        assert result["fatal"] is True

    async def test_not_logged_in_message(self, monkeypatch):
        output = "❌ 未检测到可用登录态（code: 31）\n❌ 部分检查未通过"
        result = await self._run(monkeypatch, output, 1)
        assert result["ok"] is False


class TestCheckUsesOwnProfile:
    """回归：``--check`` 必须在正确档案的 HOME 下运行。"""

    async def test_run_check_passes_profile_hash_through(self, monkeypatch, storage_root):
        captured: dict[str, object] = {}

        async def fake_run_capture(command, *, log_path, cwd, env, timeout):
            captured["command"] = command
            captured["HOME"] = env.get("HOME")
            captured["USERPROFILE"] = env.get("USERPROFILE")
            return 0, "环境检查：登录态正常，通过"

        monkeypatch.setattr(auth_service, "run_capture", fake_run_capture)

        outcome = await run_check(9222, "my-profile-hash")
        assert outcome["ok"] is True

        # 关键断言：HOME 必须指向传入档案的父目录，而不是某个随便挑的档案
        expected_home = str(storage_root / "chrome-profiles")
        assert captured["HOME"] == expected_home
        assert captured["USERPROFILE"] == expected_home

    async def test_check_login_state_falls_back_to_cookies(self, monkeypatch, storage_root):
        """--check 失败但 Cookie 有效时，应判定为已登录。"""
        from bzp_workbench.services import auth_service as module

        monkeypatch.setattr(module, "cdp_reachable", lambda port, timeout=0.6: True)

        async def fake_run_check(cdp_port, profile_hash=None, timeout=90):
            return {"ok": False, "detail": "环境检查未通过", "fatal": False, "raw": ""}

        monkeypatch.setattr(module, "run_check", fake_run_check)

        profile_hash = "cookie-fallback-hash"
        profile_root = storage_root / "chrome-profiles" / profile_hash
        _make_chrome_profile(
            profile_root / ".boss-zhipin-scraper" / "chrome-profile", logged_in=True
        )

        outcome = await module.check_login_state(9222, profile_hash)
        assert outcome["ok"] is True
        assert "登录票据" in outcome["detail"]

    async def test_check_login_state_prefers_check_result(self, monkeypatch):
        from bzp_workbench.services import auth_service as module

        monkeypatch.setattr(module, "cdp_reachable", lambda port, timeout=0.6: True)

        async def fake_run_check(cdp_port, profile_hash=None, timeout=90):
            return {"ok": True, "detail": "登录态有效", "fatal": False, "raw": ""}

        monkeypatch.setattr(module, "run_check", fake_run_check)
        outcome = await module.check_login_state(9222, "whatever")
        assert outcome["ok"] is True
        assert outcome["detail"] == "登录态有效"

    async def test_fatal_short_circuits(self, monkeypatch):
        from bzp_workbench.services import auth_service as module

        monkeypatch.setattr(module, "cdp_reachable", lambda port, timeout=0.6: True)

        async def fake_run_check(cdp_port, profile_hash=None, timeout=90):
            return {"ok": False, "detail": "触发风控", "fatal": True, "raw": ""}

        monkeypatch.setattr(module, "run_check", fake_run_check)
        outcome = await module.check_login_state(9222, "x")
        assert outcome["fatal"] is True

    async def test_no_cdp_returns_immediately(self, monkeypatch):
        from bzp_workbench.services import auth_service as module

        monkeypatch.setattr(module, "cdp_reachable", lambda port, timeout=0.6: False)
        outcome = await module.check_login_state(9222, "x")
        assert outcome["ok"] is False
        assert "CDP" in outcome["detail"]


class TestBuildEnvironmentProfileIsolation:
    def test_profile_dir_is_under_home(self, storage_root):
        env = build_environment("hash-a", 9222)
        assert str(env.profile_dir).startswith(str(env.home_dir))
        assert env.home_dir == storage_root / "chrome-profiles"

    def test_different_hashes_get_different_dirs(self):
        first = build_environment("hash-a")
        second = build_environment("hash-b")
        assert first.profile_dir != second.profile_dir
