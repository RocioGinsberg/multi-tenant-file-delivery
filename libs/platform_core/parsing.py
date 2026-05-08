"""Cross-platform parsing primitives.

These are tiny, zero-dependency helpers used by Runner, Portal, and
potentially AI services.  Canonical definitions live here — all other
modules should import from ``platform_core.parsing``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def is_blank(v: Any) -> bool:
    """Return True if *v* is None or a whitespace-only string."""
    if v is None:
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    return False


def parse_iso_datetime(s: str) -> datetime:
    """Parse an ISO-8601 string into a timezone-aware UTC datetime.

    Accepts trailing ``Z`` as UTC shorthand.  Naive datetimes are assumed UTC.
    """
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def now_utc() -> datetime:
    """Return the current time in UTC (timezone-aware)."""
    return datetime.now(timezone.utc)
