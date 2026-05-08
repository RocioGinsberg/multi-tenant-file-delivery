from __future__ import annotations

import logging
from typing import Any, Dict, List

from . import smh

logger = logging.getLogger("jobs.cosdrive.team")


async def refresh_teams() -> List[Dict[str, Any]]:
    raw_tree = await smh.fetch_team_tree()
    flat = smh.flatten_team_tree(raw_tree)
    logger.info("团队刷新完成: %d 个团队", len(flat))
    return flat


def build_team_index(
    teams: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    by_original: Dict[str, Dict[str, Any]] = {}
    by_name: Dict[str, Dict[str, Any]] = {}
    by_han: Dict[str, Dict[str, Any]] = {}

    for t in teams:
        key_orig = t["original_name"].replace("\u3000", " ").strip().lower()
        key_name = t["name"].replace("\u3000", " ").strip().lower()
        by_original[key_orig] = t
        by_name[key_name] = t
        han = smh.norm_name(t["original_name"]) or smh.norm_name(t["name"])
        if han:
            by_han[han] = t

    return {
        "by_original": by_original,
        "by_name": by_name,
        "by_han": by_han,
    }


def match_team(
    team_raw: str,
    team_index: Dict[str, Dict[str, Dict[str, Any]]],
) -> Dict[str, Any] | None:
    key = team_raw.replace("\u3000", " ").strip().lower()
    t = team_index["by_original"].get(key)
    if t:
        return t
    t = team_index["by_name"].get(key)
    if t:
        return t
    han = smh.norm_name(team_raw)
    if han:
        t = team_index["by_han"].get(han)
        if t:
            return t
    return None
