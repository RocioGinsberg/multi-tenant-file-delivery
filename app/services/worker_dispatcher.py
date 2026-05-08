"""Dispatch helpers for the standalone CosDrive local service.

Default mode runs the CosDrive upload worker as a local detached subprocess so
the extracted service can be used without Prefect.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from ..settings import settings

logger = logging.getLogger("cosdrive_local.worker_dispatcher")


def _service_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _repo_root() -> Path:
    return Path(settings.legend_app_root).resolve()


def _pythonpath() -> str:
    repo_root = _repo_root()
    service_root = _service_root()
    paths = [
        str(service_root),
        str(repo_root),
        str(repo_root / "libs" / "runner-shared" / "src"),
        str(repo_root / "libs" / "runtime-observability" / "src"),
        str(repo_root / "libs" / "platform-core" / "src"),
        str(repo_root / "libs" / "ai-contracts" / "src"),
        str(repo_root / "libs" / "data-contracts" / "src"),
    ]
    existing = os.environ.get("PYTHONPATH", "")
    if existing:
        paths.append(existing)
    return os.pathsep.join([path for path in paths if Path(path).exists()])


def dispatch_cosdrive_upload(task_id: str) -> None:
    mode = settings.cosdrive_dispatch_mode
    if mode != "local-process":
        logger.warning(
            "unsupported COSDRIVE_DISPATCH_MODE=%s; falling back to local-process",
            mode,
        )
    env = os.environ.copy()
    env.setdefault("LEGEND_APP_ROOT", str(_repo_root()))
    env["PYTHONPATH"] = _pythonpath()
    command = [
        sys.executable,
        "-m",
        "jobs.workers.cosdrive_upload_worker",
        "--task-id",
        task_id,
    ]
    subprocess.Popen(
        command,
        cwd=_service_root(),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
