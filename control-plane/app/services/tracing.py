from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import Request, Response
from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from app.core.settings import Settings, get_settings

_TRACER = trace.get_tracer("control-plane")
_TRACER_PROVIDER: TracerProvider | None = None
_CONFIGURED_KEY: tuple[str, str] | None = None


def tracing_enabled(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return bool(getattr(settings, "observability_enabled", False))


def configure_tracing(settings: Settings | None = None) -> None:
    """Configure OTLP trace export when observability is explicitly enabled."""
    global _CONFIGURED_KEY, _TRACER_PROVIDER

    settings = settings or get_settings()
    if not tracing_enabled(settings):
        return

    endpoint = _trace_export_endpoint(settings.otel_exporter_otlp_endpoint)
    key = (settings.service_name, endpoint)
    if _CONFIGURED_KEY == key:
        return

    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.service_name}),
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _TRACER_PROVIDER = provider
    _CONFIGURED_KEY = key


def shutdown_tracing() -> None:
    if _TRACER_PROVIDER is not None:
        _TRACER_PROVIDER.force_flush()


async def tracing_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    if not tracing_enabled():
        return await call_next(request)

    response: Response | None = None
    with start_trace_span(
        f"HTTP {request.method}",
        kind=SpanKind.SERVER,
        attributes={
            "http.request.method": request.method,
            "url.path": request.url.path,
            "url.scheme": request.url.scheme,
        },
    ) as span:
        try:
            response = await call_next(request)
            return response
        finally:
            route = _route_label(request)
            status_code = response.status_code if response is not None else 500
            if span is not None:
                span.update_name(f"{request.method} {route}")
                span.set_attribute("http.route", route)
                span.set_attribute("http.response.status_code", status_code)
                if status_code >= 500:
                    span.set_status(Status(StatusCode.ERROR))


@contextmanager
def start_trace_span(
    name: str,
    *,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Span | None]:
    if not tracing_enabled():
        yield None
        return

    with _TRACER.start_as_current_span(
        name,
        kind=kind,
        attributes=attributes or {},
    ) as span:
        try:
            yield span
        except Exception as exc:
            record_span_exception(span, exc)
            raise


def record_span_exception(span: Span | None, exc: BaseException) -> None:
    if span is None or not span.is_recording():
        return
    span.record_exception(exc)
    span.set_status(Status(StatusCode.ERROR, str(exc)))


def current_traceparent() -> str | None:
    if not tracing_enabled():
        return None

    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None

    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier.get("traceparent")


def _trace_export_endpoint(endpoint: str) -> str:
    normalized = endpoint.rstrip("/")
    if normalized.endswith("/v1/traces"):
        return normalized
    return f"{normalized}/v1/traces"


def _route_label(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path or "unmatched"
