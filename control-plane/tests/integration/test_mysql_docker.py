from __future__ import annotations

import asyncio
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine


MYSQL_DATABASE_URL = (
    "mysql+asyncmy://control_plane:control_plane@localhost:3306/"
    "control_plane?charset=utf8mb4"
)


@pytest.mark.docker
@pytest.mark.mysql
@pytest.mark.integration
def test_mysql_alembic_upgrade_creates_core_tables():
    if os.getenv("RUN_MYSQL_TESTS") != "1":
        pytest.skip("set RUN_MYSQL_TESTS=1 with deploy/docker-compose.yml mysql running")

    db_url = os.getenv("MYSQL_DATABASE_URL", MYSQL_DATABASE_URL)
    os.environ["DATABASE_URL"] = db_url
    try:
        from app.core.settings import get_settings

        get_settings.cache_clear()

        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", "alembic")
        alembic_cfg.set_main_option("sqlalchemy.url", db_url)
        command.upgrade(alembic_cfg, "head")

        tables = asyncio.run(_get_table_names(db_url))
    finally:
        os.environ.pop("DATABASE_URL", None)
        get_settings.cache_clear()

    assert {"alembic_version", "task", "task_item", "task_event"} <= tables


async def _get_table_names(db_url: str) -> set[str]:
    engine = create_async_engine(db_url, future=True)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names())
            )
    finally:
        await engine.dispose()
