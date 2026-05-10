from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

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
    target_name_matched: str = ""
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


def classify_zip(
    zip_bytes: bytes,
    profile: dict[str, Any],
) -> tuple[list[ClassifiedItem], ClassifySummary]:
    """Classify an uploaded zip using a static Classification Profile.

    Phase 1.5 will implement the profile engine with TDD. This scaffold keeps
    the service contract generic and prevents legacy team/drive semantics from
    leaking into new code.
    """
    raise NotImplementedError("Phase 1.5 implements classifier profile engine")


def classify_entries(
    entries: list[FileEntry],
    profile: dict[str, Any],
) -> tuple[list[ClassifiedItem], ClassifySummary]:
    """Classify normalized file entries using a static Classification Profile."""
    raise NotImplementedError("Phase 1.5 implements classifier profile engine")
