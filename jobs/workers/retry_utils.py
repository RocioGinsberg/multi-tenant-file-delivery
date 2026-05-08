from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

try:
    from fastapi import HTTPException
except Exception:  # pragma: no cover - optional import in some test envs
    HTTPException = None  # type: ignore

T = TypeVar("T")


def is_retryable_error(exc: BaseException) -> bool:
    explicit = getattr(exc, "retryable", None)
    if isinstance(explicit, bool):
        return explicit

    if HTTPException is not None and isinstance(exc, HTTPException):
        status_code = int(getattr(exc, "status_code", 0) or 0)
        return status_code == 429 or status_code >= 500

    return isinstance(exc, (TimeoutError, ConnectionError, OSError))


def run_with_attempt_retry(
    operation: Callable[[], T],
    *,
    max_attempts: int = 2,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> T:
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    attempt_no = 0
    while True:
        attempt_no += 1
        try:
            return operation()
        except Exception as exc:
            if attempt_no >= max_attempts or not is_retryable_error(exc):
                raise
            if on_retry is not None:
                on_retry(attempt_no, exc)
