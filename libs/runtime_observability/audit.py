"""Structured audit logging.

Emits JSON-line audit events to stdout.  The log pipeline (Fluentd, CloudWatch,
etc.) is responsible for routing these to a durable store.

Usage::

    from runtime_observability.audit import audit_log

    audit_log({"event": "approval_action", "user": "alice", "action": "approve"})
"""

import json
import logging
from datetime import datetime, timezone

_logger = logging.getLogger("legend.audit")
if not _logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(_h)
    _logger.setLevel(logging.INFO)


def audit_log(event: dict) -> None:
    """Emit a structured audit event as a JSON line to stdout."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "audit": True,
        **event,
    }
    _logger.info(json.dumps(record, ensure_ascii=False, default=str))
