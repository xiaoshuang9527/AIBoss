"""测试夹具。

关键点：``bzp_workbench.config.Settings`` 在导入时就实例化了，
所以必须在 import 应用代码**之前**把 ``BZP_*`` 环境变量写进
``os.environ``，并在设置里显式关闭 ``.env`` 读取，保证测试不受本地
``backend/.env`` 干扰。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# ------------------------------------------------------------------
# 先设定环境，再导入应用
# ------------------------------------------------------------------
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="bzp-test-"))
os.environ.update(
    {
        "BZP_STORAGE_ROOT": str(_TMP_ROOT / "storage"),
        "BZP_DATABASE_URL": f"sqlite+aiosqlite:///{(_TMP_ROOT / 'test.db').as_posix()}",
        "BZP_SCRAPER_DIR": str(Path(__file__).resolve().parent / "fake_scraper"),
        "BZP_SCRAPER_PYTHON": "",
        "BZP_MAX_CONCURRENT_TASKS": "2",
        "BZP_TASK_DELAY_MIN": "0",
        "BZP_TASK_DELAY_MAX": "0",
        "BZP_MAX_RETRIES": "0",
        "BZP_SECRET_KEY": "test-secret-key-not-for-production",
        "BZP_STALE_TASK_SCAN_SECONDS": "3600",
        # 测试环境没有真实 Chrome：关掉自动拉起，避免白等
        "BZP_AUTOSTART_BROWSER": "false",
        "BZP_BROWSER_START_TIMEOUT_SECONDS": "0",
        "BZP_CONFIG_ENV_FILE": "",
    }
)

import bzp_workbench.config as config_module  # noqa: E402

# 关掉 .env 文件读取，让上面的环境变量说话
config_module.Settings.model_config["env_file"] = None
config_module.get_settings.cache_clear()
config_module.settings = config_module.get_settings()

from bzp_workbench.config import settings  # noqa: E402

settings.ensure_directories()


@pytest.fixture(scope="session")
def storage_root() -> Path:
    return settings.storage_path


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    """测试结束清理临时目录。"""
    import shutil

    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


@pytest.fixture
async def app_instance():
    """构造带 lifespan 的应用实例。"""
    from bzp_workbench.main import create_app

    return create_app()


@pytest.fixture
async def client(app_instance):
    """基于 ASGITransport 的异步 HTTP 客户端（含 lifespan）。"""
    import httpx

    async with app_instance.router.lifespan_context(app_instance):
        transport = httpx.ASGITransport(app=app_instance)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client


@pytest.fixture
async def db_session():
    """直接操作数据库的会话。"""
    from bzp_workbench.db import SessionLocal, init_db

    await init_db()
    async with SessionLocal() as session:
        yield session
