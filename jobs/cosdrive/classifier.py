from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger("jobs.cosdrive.classifier")

_NUMBER_PREFIX_RE = re.compile(r"^\d+\.\s*")
_UNKNOWN_TASK_TAGS = frozenset({
    "未知文件类型", "Excel 文件", "CSV 文件", "PDF 文件",
    "文本文件", "JSON 文件", "XML 文件",
})


class ClassifiedItem:
    __slots__ = (
        "item_id", "filename", "relative_path", "ext", "file_size",
        "team_name_raw", "team_name_matched", "team_space_id", "team_org_id",
        "task_name", "category_name", "drive_dir", "drive_path",
        "team_match_method", "task_match_method", "match_score", "mapping_source",
        "severity", "error_code", "error_message", "warning_message",
    )

    def __init__(self, **kwargs):
        for slot in self.__slots__:
            setattr(self, slot, kwargs.get(slot, ""))
        if not self.item_id:
            self.item_id = uuid.uuid4().hex[:16]
        if not self.severity:
            self.severity = "ok"
        for int_field in ("file_size", "match_score"):
            val = getattr(self, int_field)
            if isinstance(val, str) and val == "":
                setattr(self, int_field, 0)

    def to_dict(self) -> Dict[str, Any]:
        return {s: getattr(self, s) for s in self.__slots__}


class ClassifySummary:
    def __init__(self):
        self.total = 0
        self.ok = 0
        self.warning = 0
        self.error = 0
        self.ignored = 0
        self.unmatched_teams = 0
        self.unmatched_tasks = 0
        self.teams_involved: set = set()
        self.has_blocking_errors = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "ok": self.ok,
            "warning": self.warning,
            "error": self.error,
            "ignored": self.ignored,
            "unmatched_teams": self.unmatched_teams,
            "unmatched_tasks": self.unmatched_tasks,
            "teams_involved": sorted(self.teams_involved),
            "has_blocking_errors": self.has_blocking_errors,
        }


def _normalize_text(text: str) -> str:
    return "".join(re.findall(r"[A-Za-z\u4e00-\u9fff]", text))


def _strip_number_prefix(name: str) -> str:
    return _NUMBER_PREFIX_RE.sub("", name).strip()


def _fuzzy_match(text: str, candidates: List[str], threshold: int = 60) -> Optional[tuple[str, int]]:
    try:
        from fuzzywuzzy import process as fuzz_process
    except ImportError:
        from thefuzz import process as fuzz_process

    if not candidates:
        return None
    result = fuzz_process.extractOne(text, candidates)
    if result and result[1] >= threshold:
        return (result[0], result[1])
    return None


def classify_files(
    file_entries: List[Dict[str, Any]],
    config: Dict[str, Any],
    teams: List[Dict[str, Any]],
    task_id: str,
) -> tuple[List[ClassifiedItem], ClassifySummary]:
    team_aliases = config.get("team_aliases", {})
    task_to_info = config.get("task_classification", {})
    description_mapping = config.get("description_mapping", {})
    mapping_config = config.get("mapping_config", {})
    suffix_priority = config.get("suffix_priority", {})
    suffix_fallback = config.get("suffix_fallback", {})
    ignored_filenames = set(config.get("ignored_filenames", []))

    fuzzy_enabled = mapping_config.get("enable_fuzzy_match", True)
    fuzzy_threshold = mapping_config.get("fuzzy_threshold", 65)
    desc_fuzzy_threshold = mapping_config.get("description_mapping_fuzzy_threshold", 70)

    valid_team_names = [t["name"] for t in teams if t.get("name")]
    team_name_to_info = {}
    for t in teams:
        team_name_to_info[t["name"]] = t
        if t.get("original_name"):
            team_name_to_info[t["original_name"]] = t

    stripped_team_index: Dict[str, str] = {}
    for name in valid_team_names:
        stripped = _strip_number_prefix(name)
        if stripped and stripped != name:
            stripped_team_index[stripped] = name

    team_name_mapping: Dict[str, str] = {}
    for t in teams:
        if t.get("name") and t.get("original_name"):
            team_name_mapping[t["name"]] = t["original_name"]

    items: List[ClassifiedItem] = []
    summary = ClassifySummary()

    for entry in sorted(file_entries, key=lambda e: e.get("relative_path", "")):
        summary.total += 1
        filename = entry["filename"]
        relative_path = entry.get("relative_path", filename)
        ext = entry.get("ext", "")
        file_size = entry.get("file_size", 0)

        if filename in ignored_filenames:
            items.append(ClassifiedItem(
                filename=filename, relative_path=relative_path,
                ext=ext, file_size=file_size,
                severity="ignored", error_code="IGNORED_FILE",
                error_message=f"文件 '{filename}' 在忽略列表中",
            ))
            summary.ignored += 1
            continue

        base = filename.rsplit(".", 1)[0] if "." in filename else filename
        parts = [p.strip() for p in re.split(r"\s*[-—–\u2018-]\s*", base)]
        team_raw = parts[-1] if len(parts) >= 2 else ""
        desc_candidate = " ".join(parts[:-1]).strip() if len(parts) >= 2 else (parts[0] if parts else "")

        team_match_method = ""
        team_matched = ""
        team_space_id = ""
        team_org_id = ""
        team_error = ""
        team_suggestion = ""
        if team_raw:
            team_matched, team_match_method, team_space_id, team_org_id, team_error, team_suggestion = _match_team(
                team_raw, team_aliases, valid_team_names, stripped_team_index, team_name_mapping, team_name_to_info,
            )

        description = _normalize_text(desc_candidate)
        task_name, task_match_method, task_score, task_mapping_source = _classify_task(
            description, ext, suffix_priority, description_mapping, task_to_info,
            suffix_fallback, fuzzy_enabled, fuzzy_threshold, desc_fuzzy_threshold,
        )

        task_error = ""
        if not task_name or task_name in _UNKNOWN_TASK_TAGS:
            task_error = f"文件 '{filename}' 的任务 '{task_name}' 未在配置中找到"

        info = task_to_info.get(task_name, {})
        if isinstance(info, dict):
            category = info.get("category", "未知类别")
            task_display = info.get("task", "")
        else:
            category = "未知类别"
            task_display = task_name

        if task_display and task_display.strip():
            drive_dir = f"{category}/{task_display}" if category != "未知类别" else task_display
        else:
            drive_dir = category if category != "未知类别" else "未分类"

        drive_path = f"{drive_dir}/{filename}"
        final_team = team_name_mapping.get(team_matched, team_matched) if team_matched else ""

        severity = "ok"
        error_code = ""
        error_message = ""
        warning_message = ""

        if team_error:
            severity = "error"
            error_code = "TEAM_NOT_FOUND"
            error_message = team_error
            if team_suggestion:
                warning_message = f"建议团队: {team_suggestion}"
            summary.unmatched_teams += 1
            summary.has_blocking_errors = True
            summary.error += 1
        elif task_error:
            severity = "error"
            error_code = "TASK_NOT_FOUND"
            error_message = task_error
            summary.unmatched_tasks += 1
            summary.has_blocking_errors = True
            summary.error += 1
        elif task_score and task_score < 100:
            severity = "warning"
            warning_message = f"任务匹配为模糊结果，分数={task_score}"
            summary.warning += 1
        else:
            summary.ok += 1

        if final_team:
            summary.teams_involved.add(final_team)

        items.append(ClassifiedItem(
            filename=filename,
            relative_path=relative_path,
            ext=ext,
            file_size=file_size,
            team_name_raw=team_raw,
            team_name_matched=final_team,
            team_space_id=team_space_id,
            team_org_id=team_org_id,
            task_name=task_display or task_name,
            category_name=category,
            drive_dir=drive_dir,
            drive_path=drive_path,
            team_match_method=team_match_method,
            task_match_method=task_match_method,
            match_score=task_score,
            mapping_source=task_mapping_source,
            severity=severity,
            error_code=error_code,
            error_message=error_message,
            warning_message=warning_message,
        ))

    return items, summary


def _match_team(
    team_raw: str,
    team_aliases: Dict[str, str],
    valid_team_names: List[str],
    stripped_team_index: Dict[str, str],
    team_name_mapping: Dict[str, str],
    team_name_to_info: Dict[str, Dict[str, Any]],
) -> tuple[str, str, str, str, str, str]:
    if team_raw in team_aliases:
        team_name = team_aliases[team_raw]
        team_info = team_name_to_info.get(team_name) or team_name_to_info.get(team_name_mapping.get(team_name, ""))
        return (
            team_name,
            "team_aliases",
            str((team_info or {}).get("spaceId", "")),
            str((team_info or {}).get("orgId", "")),
            "",
            "",
        )

    if team_raw in valid_team_names:
        team_info = team_name_to_info.get(team_raw, {})
        return team_raw, "exact", str(team_info.get("spaceId", "")), str(team_info.get("orgId", "")), "", ""

    stripped_raw = _strip_number_prefix(team_raw)
    if stripped_raw in stripped_team_index:
        matched = stripped_team_index[stripped_raw]
        team_info = team_name_to_info.get(matched, {})
        return matched, "strip_prefix_exact", str(team_info.get("spaceId", "")), str(team_info.get("orgId", "")), "", ""

    fuzzy = _fuzzy_match(team_raw, valid_team_names, threshold=70)
    if fuzzy:
        suggestion, _ = fuzzy
        return "", "", "", "", f"未匹配团队: {team_raw}", suggestion
    return "", "", "", "", f"未匹配团队: {team_raw}", ""


def _classify_task(
    description: str,
    ext: str,
    suffix_priority: Dict[str, str],
    description_mapping: Dict[str, str],
    task_to_info: Dict[str, Any],
    suffix_fallback: Dict[str, str],
    fuzzy_enabled: bool,
    fuzzy_threshold: int,
    desc_fuzzy_threshold: int,
) -> tuple[str, str, int, str]:
    suffix_hit = suffix_priority.get(ext, "")
    if suffix_hit:
        return suffix_hit, "suffix_priority", 100, "suffix_priority"

    if description in description_mapping:
        return description_mapping[description], "description_mapping_exact", 100, "description_mapping"

    if fuzzy_enabled and description_mapping:
        fuzzy_desc = _fuzzy_match(description, list(description_mapping.keys()), threshold=desc_fuzzy_threshold)
        if fuzzy_desc:
            matched_desc, score = fuzzy_desc
            return description_mapping[matched_desc], "description_mapping_fuzzy", score, "description_mapping"

    task_names = list(task_to_info.keys())
    if description in task_names:
        return description, "task_exact", 100, "task_classification"

    if fuzzy_enabled and task_names:
        fuzzy_task = _fuzzy_match(description, task_names, threshold=fuzzy_threshold)
        if fuzzy_task:
            matched_task, score = fuzzy_task
            return matched_task, "task_fuzzy", score, "task_classification"

    fallback = suffix_fallback.get(ext, "")
    if fallback:
        return fallback, "suffix_fallback", 60, "suffix_fallback"
    return "未知文件类型", "unknown", 0, ""
