"""Lightweight audit logging for the standalone CosDrive local service."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("cosdrive_local.audit")


def audit_log(payload: dict[str, Any]) -> None:
    logger.info("audit %s", json.dumps(payload, ensure_ascii=False, default=str))
