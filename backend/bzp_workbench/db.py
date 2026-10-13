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
    """建表 + 补齐新增列。

    生产环境建议改用 Alembic 迁移，见 ``deploy/sql/``。
    """
    settings.ensure_directories()
    # 确保模型已被导入并注册到 Base.metadata
    from . import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)


#: 需要自动补齐的列：``(表名, 列名, 建列 DDL 片段)``。
#:
#: 为什么需要这个
#: --------------
#: ``Base.metadata.create_all`` 只会**创建缺失的表**，对已存在的表不会加列。
#: 于是「给旧表加一个字段」在本地开发时表现为：
#: 代码里用了新字段 → 数据库里没有这一列 → 运行时报 no such column，
#: 而报错位置往往离真正的原因很远（比如在某个后台任务里）。
#:
#: 这里做一个极简的补齐：只加列、不改类型、不删列。
#: 够用且安全 —— 加列是向后兼容的。复杂变更仍然应该写迁移脚本。
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # 记录任务是由哪个对话发起的，用于把进度推回该对话（P3）
    ("scrape_tasks", "conversation_id", "INTEGER"),
)


def _add_missing_columns(connection) -> None:
    """用 SQLAlchemy inspector 检查并补齐 ``_ADDED_COLUMNS`` 里的列。"""
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(connection)
    existing_tables = set(inspector.get_table_names())

    for table, column, ddl_type in _ADDED_COLUMNS:
        if table not in existing_tables:
            continue  # 新库由 create_all 直接建好，不需要补
        columns = {item["name"] for item in inspector.get_columns(table)}
        if column in columns:
            continue
        # 标识符来自上面的常量，不是用户输入，拼接是安全的
        connection.exec_driver_sql(
            f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"
        )


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
