"""Platform-level error types and error envelope.

These are the canonical error codes shared across all services.
Services should map internal exceptions to these codes at their boundaries.

ErrorEnvelope is the standard error response shape for all API errors.
See docs/50-contracts/error-and-status-contracts.md.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ErrorCode(str, Enum):
    BAD_REQUEST = "BAD_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    TIMEOUT = "TIMEOUT"
    INTERNAL = "INTERNAL"


class PlatformError(Exception):
    """Base error carrying a canonical ErrorCode."""

    def __init__(self, code: ErrorCode, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else str(code))


class ErrorEnvelope(BaseModel):
    """Standard error response shape for all API errors.

    All services should return this shape for non-2xx responses.
    """

    error_code: str = Field(description="Canonical ErrorCode value")
    error_type: str = Field(default="", description="Service-specific error type")
    retryable: bool = Field(default=False)
    user_message: str = Field(default="", description="User-facing message")
    operator_message: str = Field(default="", description="Operator/debug message")
    trace_id: str = Field(default="")
    request_id: str = Field(default="")
