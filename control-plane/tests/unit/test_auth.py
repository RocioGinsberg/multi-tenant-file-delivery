from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.settings import Settings
from app.services.auth import CurrentActor, resolve_current_actor


def test_resolve_current_actor_uses_default_actor_when_headers_missing():
    actor = resolve_current_actor({}, Settings())

    assert actor == CurrentActor(
        tenant_id="hq",
        user_id="local-user",
        role="hq_uploader",
    )
    assert actor.is_hq is True
    assert actor.workspace_access_scope == "owner"
    assert actor.can_write_tasks is True


def test_resolve_current_actor_prefers_dev_headers():
    actor = resolve_current_actor(
        {
            "X-Actor-Tenant": "subsidiary-a",
            "X-Actor-User": "sub-user",
            "X-Actor-Role": "subsidiary_viewer",
        },
        Settings(),
    )

    assert actor == CurrentActor(
        tenant_id="subsidiary-a",
        user_id="sub-user",
        role="subsidiary_viewer",
    )
    assert actor.can_write_tasks is False
    assert actor.workspace_access_scope == "target"


def test_current_actor_hq_scope_comes_from_role_not_tenant_literal():
    actor = CurrentActor(
        tenant_id="headquarters",
        user_id="hq-user",
        role="hq_admin",
    )

    assert actor.is_hq is True
    assert actor.workspace_access_scope == "owner"


def test_resolve_current_actor_rejects_missing_actor_when_default_disabled():
    settings = Settings(auth_default_actor_enabled=False)

    with pytest.raises(HTTPException) as exc_info:
        resolve_current_actor({}, settings)

    assert exc_info.value.status_code == 401


def test_resolve_current_actor_rejects_invalid_role():
    with pytest.raises(HTTPException) as exc_info:
        resolve_current_actor(
            {
                "X-Actor-Tenant": "hq",
                "X-Actor-User": "local-user",
                "X-Actor-Role": "owner",
            },
            Settings(),
        )

    assert exc_info.value.status_code == 403


def test_current_actor_require_task_writer_rejects_subsidiary_viewer():
    actor = CurrentActor(
        tenant_id="subsidiary-a",
        user_id="sub-user",
        role="subsidiary_viewer",
    )

    with pytest.raises(HTTPException) as exc_info:
        actor.require_task_writer()

    assert exc_info.value.status_code == 403


def test_current_actor_event_payload_is_low_cardinality_actor_context():
    actor = CurrentActor(tenant_id="hq", user_id="local-user", role="hq_uploader")

    assert actor.to_event_payload() == {
        "actor_tenant_id": "hq",
        "actor_user_id": "local-user",
        "actor_role": "hq_uploader",
    }
