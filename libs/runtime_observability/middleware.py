"""Starlette/FastAPI middleware for request tracing and access logging.

Both middlewares are framework-agnostic within the Starlette ecosystem and
can be used by any FastAPI service in the platform.

Usage::

    from runtime_observability.middleware import RequestIdMiddleware, AccessLogMiddleware

    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIdMiddleware)
"""

import json
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_access_logger = logging.getLogger("legend.access")
if not _access_logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(message)s"))
    _access_logger.addHandler(_h)
    _access_logger.setLevel(logging.INFO)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Propagate or generate an X-Request-Id header on every request."""

    async def dispatch(self, request: Request, call_next) -> Response:
        rid = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        request.state.request_id = rid
        resp: Response = await call_next(request)
        resp.headers["X-Request-Id"] = rid
        return resp


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Log method, path, status, and duration for every request as JSON."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.time()
        resp = await call_next(request)
        dur_ms = int((time.time() - start) * 1000)
        rid = getattr(request.state, "request_id", "")
        _access_logger.info(
            json.dumps(
                {
                    "request_id": rid,
                    "method": request.method,
                    "path": request.url.path,
                    "status": resp.status_code,
                    "duration_ms": dur_ms,
                },
                ensure_ascii=False,
            )
        )
        return resp
