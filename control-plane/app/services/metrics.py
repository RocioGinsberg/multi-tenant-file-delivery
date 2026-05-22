from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

from app.core.settings import get_settings

REGISTRY = CollectorRegistry()

HTTP_REQUESTS_TOTAL = Counter(
    "control_plane_http_requests_total",
    "Total HTTP requests handled by the control plane.",
    ("method", "route", "status"),
    registry=REGISTRY,
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "control_plane_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route", "status"),
    registry=REGISTRY,
)
TASK_OPERATIONS_TOTAL = Counter(
    "control_plane_task_operations_total",
    "Total task workflow operations handled by the control plane.",
    ("operation", "status"),
    registry=REGISTRY,
)
TASK_OPERATION_DURATION_SECONDS = Histogram(
    "control_plane_task_operation_duration_seconds",
    "Task workflow operation duration in seconds.",
    ("operation", "status"),
    registry=REGISTRY,
)
DELIVERY_EVENTS_TOTAL = Counter(
    "control_plane_delivery_events_total",
    "Total delivery operations handled by the control plane.",
    ("operation", "transport", "status"),
    registry=REGISTRY,
)
DELIVERY_EVENT_DURATION_SECONDS = Histogram(
    "control_plane_delivery_event_duration_seconds",
    "Delivery operation duration in seconds.",
    ("operation", "transport", "status"),
    registry=REGISTRY,
)


async def metrics_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    if not metrics_enabled():
        return await call_next(request)

    start = time.perf_counter()
    response: Response | None = None
    status = "500"
    try:
        response = await call_next(request)
        status = str(response.status_code)
        return response
    finally:
        route = _route_label(request)
        elapsed = time.perf_counter() - start
        HTTP_REQUESTS_TOTAL.labels(request.method, route, status).inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(request.method, route, status).observe(elapsed)


def metrics_enabled() -> bool:
    return bool(get_settings().metrics_enabled)


def metrics_response() -> Response:
    payload = generate_latest(REGISTRY)
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)


def record_task_operation(operation: str, status: str, duration_seconds: float) -> None:
    if not metrics_enabled():
        return
    TASK_OPERATIONS_TOTAL.labels(operation, status).inc()
    TASK_OPERATION_DURATION_SECONDS.labels(operation, status).observe(duration_seconds)


def record_delivery_event(
    operation: str,
    transport: str,
    status: str,
    duration_seconds: float,
) -> None:
    if not metrics_enabled():
        return
    DELIVERY_EVENTS_TOTAL.labels(operation, transport, status).inc()
    DELIVERY_EVENT_DURATION_SECONDS.labels(operation, transport, status).observe(duration_seconds)


def _route_label(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path or "unmatched"
