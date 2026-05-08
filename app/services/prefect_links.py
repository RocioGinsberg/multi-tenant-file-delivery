"""Helpers for optional Prefect UI deeplinks."""

from __future__ import annotations

from ..settings import settings


def build_prefect_flow_run_url(flow_run_id: str | None) -> str | None:
    run_id = (flow_run_id or "").strip()
    base_url = (settings.prefect_ui_base_url or "").strip().rstrip("/")
    if not run_id or not base_url:
        return None
    return f"{base_url}/flow-runs/flow-run/{run_id}"
