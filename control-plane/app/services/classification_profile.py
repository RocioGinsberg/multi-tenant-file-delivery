from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TargetConfig:
    key: str
    aliases: list[str] = field(default_factory=list)
    strip_number_prefix: bool = False  # "12. acme" 匹配 "acme"


@dataclass
class DocumentTypeConfig:
    category: str  # 写入路径模板的上层分类


@dataclass
class MatchingConfig:
    enable_fuzzy_match: bool = True
    fuzzy_threshold: int = 70
    description_fuzzy_threshold: int = 70


@dataclass
class EntryFilterConfig:
    ignored_filenames: list[str] = field(default_factory=list)
    ignored_prefixes: list[str] = field(default_factory=list)  # 如 "__MACOSX"


@dataclass
class TargetExtractionConfig:
    strategy: str = "directory_or_filename"
    # "directory_or_filename"：有顶层目录→目录名；根目录文件→filename_segment 兜底
    # "broadcast"：全包归一个 target（broadcast_target 必填）
    # "filename_segment"：legacy，全用文件名解析
    delimiters: list[str] = field(default_factory=lambda: ["-", "—", "–", "’", "-"])
    broadcast_target: str | None = None


@dataclass
class ProfileConfig:
    version: str
    targets: list[TargetConfig]
    document_types: dict[str, DocumentTypeConfig]  # key → config
    suffix_priority: dict[str, str]                # ".xlsx" → document_type key
    description_mapping: dict[str, str]            # "月报" → document_type key
    suffix_fallback: dict[str, str]
    entry_filters: EntryFilterConfig = field(default_factory=EntryFilterConfig)
    path_template: str = "{category}/{document_type}/{filename}"
    matching_config: MatchingConfig = field(default_factory=MatchingConfig)
    target_extraction: TargetExtractionConfig = field(default_factory=TargetExtractionConfig)
