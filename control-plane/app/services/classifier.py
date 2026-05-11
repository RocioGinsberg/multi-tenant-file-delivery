from __future__ import annotations

import io
import re
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any, Literal

from app.services.classification_profile import ProfileConfig

Severity = Literal["ok", "warning", "error", "ignored"]


@dataclass(frozen=True, slots=True)
class FileEntry:
    """A normalized file discovered in an uploaded archive."""

    src_path: str
    filename: str
    ext: str = ""
    file_size: int = 0


@dataclass(frozen=True, slots=True)
class ClassifiedItem:
    """Classifier output ready to be persisted as a task_item row."""

    src_path: str
    filename: str
    ext: str = ""
    file_size: int = 0
    target_name_raw: str = ""
    target_name_matched: str | None = None
    document_type: str = ""
    category_name: str = ""
    dst_dir: str = ""
    dst_path: str = ""
    target_match_method: str = ""
    document_match_method: str = ""
    match_score: int = 0
    mapping_source: str = ""
    severity: Severity = "ok"
    error_code: str = ""
    error_message: str = ""
    warning_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ClassifySummary:
    """Aggregate counts for preview and task summary_json."""

    total: int = 0
    ok: int = 0
    warning: int = 0
    error: int = 0
    ignored: int = 0
    unmatched_targets: int = 0
    unmatched_document_types: int = 0
    targets_involved: set[str] = field(default_factory=set)
    has_blocking_errors: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "ok": self.ok,
            "warning": self.warning,
            "error": self.error,
            "ignored": self.ignored,
            "unmatched_targets": self.unmatched_targets,
            "unmatched_document_types": self.unmatched_document_types,
            "targets_involved": sorted(self.targets_involved),
            "has_blocking_errors": self.has_blocking_errors,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _decode_zip_filename(info: zipfile.ZipInfo) -> str:
    """Return a properly decoded filename from a ZipInfo entry.

    Python's zipfile may set the UTF-8 flag (0x800) in the central directory
    even when the original intent was GBK — this happens because ZipInfo re-encodes
    non-ASCII filenames stored as latin-1 carriers.

    Heuristic: if the filename contains characters in the latin-1 supplemental block
    (U+0080..U+00FF), attempt GBK recovery (encode to latin-1, decode as GBK).
    If that fails or the chars are outside latin-1 (genuine Unicode), keep as-is.
    """
    filename = info.filename
    # Check for latin-1 supplemental characters — indicator of GBK-via-latin-1 carrier
    if any("\x80" <= c <= "\xff" for c in filename):
        try:
            raw = filename.encode("latin-1")
            return raw.decode("gbk")
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
    return filename


def _is_path_traversal(src_path: str) -> bool:
    """Return True if the path contains traversal sequences."""
    if src_path.startswith("/"):
        return True
    parts = PurePosixPath(src_path).parts
    for part in parts:
        if part == "..":
            return True
    return False


def _extract_target_raw(
    src_path: str,
    filename: str,
    profile: ProfileConfig,
) -> tuple[str, str]:
    """Return (raw_target, error_code).

    error_code is '' when extraction succeeded, 'unknown_target' when it failed.
    """
    strategy = profile.target_extraction.strategy

    if strategy == "broadcast":
        broadcast = profile.target_extraction.broadcast_target or ""
        return broadcast, ""

    # strategy == "directory_or_filename"
    parts = PurePosixPath(src_path).parts
    # parts[0] is the filename itself for root-level files,
    # parts[0] is top-level directory for nested files.
    if len(parts) >= 2:
        # File is inside a directory: first part is top-level dir
        return parts[0], ""

    # Root-level file: fall back to filename_segment
    return _filename_segment(filename, profile)


def _filename_segment(
    filename: str,
    profile: ProfileConfig,
) -> tuple[str, str]:
    """Split filename stem by delimiters and take the last segment as raw target.

    Returns (raw, '') on success, ('', 'unknown_target') on failure.
    """
    stem = PurePosixPath(filename).stem
    delimiters = profile.target_extraction.delimiters

    # Build a regex that splits on any of the delimiters
    if not delimiters:
        return "", "unknown_target"

    # Escape each delimiter and join with |
    pattern = "|".join(re.escape(d) for d in delimiters)
    segments = re.split(pattern, stem)

    if len(segments) >= 2:
        last = segments[-1].strip()
        if last:
            return last, ""

    return "", "unknown_target"


def _resolve_target(
    raw: str,
    profile: ProfileConfig,
) -> tuple[str | None, str]:
    """Resolve raw target string to a target key.

    Returns (matched_key, match_method) where matched_key is None on failure.
    """
    raw_lower = raw.lower()

    for tc in profile.targets:
        # Exact match (case-insensitive)
        if tc.key.lower() == raw_lower:
            return tc.key, "exact"

        # Alias match (case-insensitive)
        for alias in tc.aliases:
            if alias.lower() == raw_lower:
                return tc.key, "alias"

        # Strip number prefix match
        if tc.strip_number_prefix:
            stripped = re.sub(r"^\d+\.\s*", "", tc.key)
            if stripped.lower() == raw_lower:
                return tc.key, "strip_number_prefix"

    return None, ""


def _resolve_document_type(
    filename: str,
    ext: str,
    profile: ProfileConfig,
) -> tuple[str, str, int]:
    """Determine document type for a file.

    Priority: suffix_priority > description_mapping (exact then fuzzy) > suffix_fallback.

    Returns (document_type, match_method, match_score).
    document_type is '' when nothing matched.
    """
    # 1. suffix_priority
    if ext in profile.suffix_priority:
        return profile.suffix_priority[ext], "suffix_priority", 100

    # 2. description_mapping — exact substring match against stem
    stem = PurePosixPath(filename).stem
    for keyword, doc_type in profile.description_mapping.items():
        if keyword in stem:
            return doc_type, "description_exact", 100

    # 3. description_mapping — fuzzy match (if enabled)
    if profile.matching_config.enable_fuzzy_match and profile.description_mapping:
        import difflib

        threshold = profile.matching_config.description_fuzzy_threshold
        best_score = 0.0
        best_doc_type = ""
        for keyword, doc_type in profile.description_mapping.items():
            score = difflib.SequenceMatcher(None, keyword, stem).ratio() * 100
            if score > best_score:
                best_score = score
                best_doc_type = doc_type
        if best_score >= threshold:
            return best_doc_type, "description_fuzzy", int(best_score)

    # 4. suffix_fallback
    if ext in profile.suffix_fallback:
        return profile.suffix_fallback[ext], "suffix_fallback", 0

    return "", "unmatched", 0


def _render_path(
    profile: ProfileConfig,
    category: str,
    document_type: str,
    filename: str,
) -> tuple[str, str]:
    """Render dst_path from path_template.

    Returns (dst_path, error_code). error_code is 'path_render_error' when
    the rendered path contains '..'.
    """
    rendered = profile.path_template.format(
        category=category,
        document_type=document_type,
        filename=filename,
    )
    # Check for path traversal in rendered path
    parts = PurePosixPath(rendered).parts
    for part in parts:
        if part == "..":
            return "", "path_render_error"
    return rendered, ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_zip(
    zip_bytes: bytes,
    profile: ProfileConfig,
) -> tuple[list[ClassifiedItem], ClassifySummary]:
    """Classify an uploaded zip using a static Classification Profile."""
    summary = ClassifySummary()
    items: list[ClassifiedItem] = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        for info in zf.infolist():
            src_path = _decode_zip_filename(info)

            # Skip pure directory entries
            if src_path.endswith("/"):
                continue

            summary.total += 1
            filename = PurePosixPath(src_path).name
            ext = PurePosixPath(filename).suffix.lower()

            # --- Safety: path traversal ---
            if _is_path_traversal(src_path):
                item = ClassifiedItem(
                    src_path=src_path,
                    filename=filename,
                    ext=ext,
                    file_size=info.file_size,
                    severity="error",
                    error_code="path_traversal",
                    error_message=f"Path traversal detected: {src_path}",
                )
                items.append(item)
                summary.error += 1
                summary.has_blocking_errors = True
                continue

            # --- Entry filter: ignored filenames ---
            if filename in profile.entry_filters.ignored_filenames:
                summary.ignored += 1
                continue

            # --- Ignored prefixes ---
            skip_prefix = False
            for prefix in profile.entry_filters.ignored_prefixes:
                if src_path.startswith(prefix):
                    skip_prefix = True
                    break
            if skip_prefix:
                summary.ignored += 1
                continue

            # --- Target extraction ---
            raw_target, target_error = _extract_target_raw(src_path, filename, profile)

            if target_error == "unknown_target":
                item = ClassifiedItem(
                    src_path=src_path,
                    filename=filename,
                    ext=ext,
                    file_size=info.file_size,
                    target_name_raw=raw_target,
                    target_name_matched=None,
                    severity="error",
                    error_code="unknown_target",
                    error_message="Could not determine target from filename",
                )
                items.append(item)
                summary.error += 1
                summary.has_blocking_errors = True
                summary.unmatched_targets += 1
                continue

            # --- Target resolution ---
            if profile.target_extraction.strategy == "broadcast":
                # broadcast: raw_target IS the broadcast target key, resolve directly
                matched_key = raw_target
                match_method = "broadcast"
                if not matched_key:
                    matched_key = None
                    match_method = ""
            else:
                matched_key, match_method = _resolve_target(raw_target, profile)

            if matched_key is None:
                item = ClassifiedItem(
                    src_path=src_path,
                    filename=filename,
                    ext=ext,
                    file_size=info.file_size,
                    target_name_raw=raw_target,
                    target_name_matched=None,
                    severity="error",
                    error_code="unknown_target",
                    error_message=f"Target '{raw_target}' did not match any known target",
                )
                items.append(item)
                summary.error += 1
                summary.has_blocking_errors = True
                summary.unmatched_targets += 1
                continue

            # --- Document type classification ---
            doc_type, doc_match_method, match_score = _resolve_document_type(
                filename, ext, profile
            )

            # --- Category lookup ---
            category_name = ""
            if doc_type and doc_type in profile.document_types:
                category_name = profile.document_types[doc_type].category

            # --- Path rendering ---
            dst_path = ""
            path_error = ""
            if doc_type and category_name:
                dst_path, path_error = _render_path(
                    profile, category_name, doc_type, filename
                )

            if path_error:
                item = ClassifiedItem(
                    src_path=src_path,
                    filename=filename,
                    ext=ext,
                    file_size=info.file_size,
                    target_name_raw=raw_target,
                    target_name_matched=matched_key,
                    target_match_method=match_method,
                    document_type=doc_type,
                    category_name=category_name,
                    severity="error",
                    error_code="path_render_error",
                    error_message="Rendered path contains '..'",
                )
                items.append(item)
                summary.error += 1
                summary.has_blocking_errors = True
                continue

            dst_dir = str(PurePosixPath(dst_path).parent) if dst_path else ""
            if dst_dir == ".":
                dst_dir = ""

            severity: Severity = "ok"
            if not doc_type:
                severity = "warning"
                summary.unmatched_document_types += 1

            item = ClassifiedItem(
                src_path=src_path,
                filename=filename,
                ext=ext,
                file_size=info.file_size,
                target_name_raw=raw_target,
                target_name_matched=matched_key,
                document_type=doc_type,
                category_name=category_name,
                dst_dir=dst_dir,
                dst_path=dst_path,
                target_match_method=match_method,
                document_match_method=doc_match_method,
                match_score=match_score,
                severity=severity,
            )
            items.append(item)

            if severity == "ok":
                summary.ok += 1
                if matched_key:
                    summary.targets_involved.add(matched_key)
            elif severity == "warning":
                summary.warning += 1

    summary.has_blocking_errors = summary.error > 0

    return items, summary


def classify_entries(
    entries: list[FileEntry],
    profile: dict[str, Any],
) -> tuple[list[ClassifiedItem], ClassifySummary]:
    """Classify normalized file entries using a static Classification Profile."""
    raise NotImplementedError("Phase 1.5 implements classifier profile engine")
