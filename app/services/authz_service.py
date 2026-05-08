"""Minimal auth helpers for the standalone CosDrive local service."""

from __future__ import annotations

from fastapi import HTTPException, Request

from ..settings import settings


def get_user(request: Request) -> dict:
    if settings.auth_mode == "header":
        user = (
            request.headers.get("X-User")
            or request.headers.get("X-User-ID")
            or "anonymous"
        )
        role = request.headers.get("X-User-Role", "viewer")
        return {"user": user, "role": role}
    return {"user": "anonymous", "role": "viewer"}


def require_auth(request: Request) -> dict:
    info = get_user(request)
    if info["user"] == "anonymous":
        raise HTTPException(status_code=401, detail="authentication required")
    return info


def validate_drive_path(drive_path: str) -> None:
    allowed = settings.cosdrive_allowed_prefixes
    if not allowed:
        return
    normalized = drive_path.rstrip("/") + "/"
    if not any(normalized.startswith(prefix) for prefix in allowed):
        raise HTTPException(
            status_code=403,
            detail=f"drive_path not in allowed prefixes: {allowed}",
        )
