"""Portal DB helpers for the standalone CosDrive local service."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

from .settings import settings

logger = logging.getLogger("cosdrive_local.db")

_portal_pool = None


def _get_portal_pool():
    import psycopg2.pool

    global _portal_pool
    if _portal_pool is None or _portal_pool.closed:
        _portal_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=settings.portal_database_url,
        )
    return _portal_pool


@contextmanager
def get_portal_conn() -> Generator:
    pool = _get_portal_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


@contextmanager
def get_portal_cursor(*, dict_cursor: bool = True) -> Generator:
    import psycopg2.extras

    with get_portal_conn() as conn:
        factory = psycopg2.extras.RealDictCursor if dict_cursor else None
        with conn.cursor(cursor_factory=factory) as cur:
            yield cur


def close_pools() -> None:
    global _portal_pool
    if _portal_pool is not None:
        try:
            if not _portal_pool.closed:
                _portal_pool.closeall()
        except Exception as exc:
            logger.warning("error closing portal pool: %s", exc)
    _portal_pool = None
