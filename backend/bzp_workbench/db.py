"""数据库会话与初始化。

默认使用 SQLite（开箱即用）；把 ``BZP_DATABASE_URL`` 换成
``postgresql+asyncpg://`` 或 ``mysql+aiomysql://`` 即可切到生产数据库。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def _build_engine() -> AsyncEngine:
    kwargs: dict = {
        "echo": settings.db_echo,
        "future": True,
        "pool_pre_ping": True,
    }
    if settings.is_sqlite:
        # SQLite 不需要连接池参数，且 aiosqlite 默认单写者，
        # 这里显式关掉线程检查以便在异步上下文里复用连接。
        kwargs.pop("pool_pre_ping", None)
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update({"pool_size": 10, "max_overflow": 20, "pool_recycle": 1800})
    return create_async_engine(settings.database_url, **kwargs)


engine: AsyncEngine = _build_engine()

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db() -> None:
    """建表。生产环境建议改用 Alembic 迁移，见 deploy/sql/。"""
    settings.ensure_directories()
    # 确保模型已被导入并注册到 Base.metadata
    from . import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_db() -> None:
    await engine.dispose()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：每个请求一个会话。"""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
