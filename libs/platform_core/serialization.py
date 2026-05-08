"""Cross-platform DB row serialization.

Converts psycopg2 RealDictRow (or any dict-like row) into a
JSON-safe dict.  Used by Portal repos and potentially query-service.
"""

from __future__ import annotations

from typing import Any


def serialize_db_row(row: Any) -> dict:
    """Convert a DB row to a JSON-serialisable dict.

    Handles:
      - Decimal → float
      - date / datetime → str (ISO format)
      - Everything else → pass-through
    """
    out: dict = {}
    for k, v in dict(row).items():
        if v is None:
            out[k] = None
        elif hasattr(v, "as_tuple"):
            # Decimal
            out[k] = float(v)
        elif hasattr(v, "isoformat"):
            # date / datetime
            out[k] = str(v)
        else:
            out[k] = v
    return out
