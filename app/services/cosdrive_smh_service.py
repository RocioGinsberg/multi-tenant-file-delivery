"""Portal compatibility wrapper for execution-side SMH helpers.

Execution-side SMH protocol logic now lives in `jobs.cosdrive.smh`.
Portal keeps this module only as a temporary compatibility import path.
"""

from __future__ import annotations

from jobs.cosdrive.smh import (  # noqa: F401
    create_directory,
    ensure_directory,
    ensure_user_token,
    fetch_team_tree,
    flatten_team_tree,
    get_access_token,
    norm_name,
    upload_single_file,
)
