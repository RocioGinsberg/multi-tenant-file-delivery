from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.core.settings import Settings, get_settings

VALID_ROLES = {
    "hq_admin",
    "hq_uploader",
    "subsidiary_admin",
    "subsidiary_viewer",
}
TASK_WRITER_ROLES = {
    "hq_admin",
    "hq_uploader",
}


@dataclass(frozen=True, slots=True)
class CurrentActor:
    tenant_id: str
    user_id: str
    role: str

    @property
    def is_hq(self) -> bool:
        return self.tenant_id == "hq"

    @property
    def can_write_tasks(self) -> bool:
        return self.role in TASK_WRITER_ROLES

    def require_role(self, *allowed_roles: str) -> CurrentActor:
        if self.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role {self.role!r} is not allowed",
            )
        return self

    def require_task_writer(self) -> CurrentActor:
        return self.require_role(*TASK_WRITER_ROLES)

    def to_event_payload(self) -> dict[str, str]:
        return {
            "actor_tenant_id": self.tenant_id,
            "actor_user_id": self.user_id,
            "actor_role": self.role,
        }


def _parse_actor_from_headers(
    headers: Mapping[str, str],
    settings: Settings,
) -> CurrentActor:
    if not settings.auth_allow_dev_headers:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Dev actor headers are disabled",
        )

    tenant_id = headers.get(settings.auth_actor_tenant_header, "").strip()
    user_id = headers.get(settings.auth_actor_user_header, "").strip()
    role = headers.get(settings.auth_actor_role_header, "").strip()

    if not tenant_id or not user_id or not role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing actor headers",
        )
    if role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Invalid actor role: {role!r}",
        )
    return CurrentActor(tenant_id=tenant_id, user_id=user_id, role=role)


def resolve_current_actor(
    headers: Mapping[str, str],
    settings: Settings | None = None,
) -> CurrentActor:
    settings = settings or get_settings()

    has_actor_headers = any(
        headers.get(header_name, "").strip()
        for header_name in (
            settings.auth_actor_tenant_header,
            settings.auth_actor_user_header,
            settings.auth_actor_role_header,
        )
    )
    if has_actor_headers:
        return _parse_actor_from_headers(headers, settings)

    if not settings.auth_default_actor_enabled:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current actor is required",
        )

    if settings.auth_default_role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Invalid default actor role: {settings.auth_default_role!r}",
        )
    return CurrentActor(
        tenant_id=settings.auth_default_tenant_id,
        user_id=settings.auth_default_user_id,
        role=settings.auth_default_role,
    )


async def get_current_actor(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> CurrentActor:
    return resolve_current_actor(request.headers, settings)
