"""Phase 1.5 TDD — Classifier Profile Engine (red phase).

All 26 tests should FAIL with NotImplementedError until classify_zip is implemented.
"""
from __future__ import annotations

import io
import zipfile

from app.services.classification_profile import (
    DocumentTypeConfig,
    EntryFilterConfig,
    MatchingConfig,
    ProfileConfig,
    TargetConfig,
    TargetExtractionConfig,
)
from app.services.classifier import classify_zip

# ---------------------------------------------------------------------------
# Profile Fixtures (defined inline, no disk IO)
# ---------------------------------------------------------------------------

PROFILE_A = ProfileConfig(
    version="1.0",
    targets=[
        TargetConfig(key="acme", aliases=["ACME"]),
        TargetConfig(key="globex"),
    ],
    document_types={
        "monthly": DocumentTypeConfig(category="reports"),
        "contract": DocumentTypeConfig(category="legal"),
    },
    suffix_priority={".pdf": "contract"},
    description_mapping={"月报": "monthly", "合同": "contract"},
    suffix_fallback={".xlsx": "monthly"},
    entry_filters=EntryFilterConfig(ignored_filenames=[".DS_Store", "Thumbs.db"]),
    path_template="{category}/{document_type}/{filename}",
    target_extraction=TargetExtractionConfig(strategy="directory_or_filename"),
)

PROFILE_B = ProfileConfig(
    version="1.0",
    targets=[
        TargetConfig(key="alpha"),
    ],
    document_types={
        "report": DocumentTypeConfig(category="docs"),
    },
    suffix_priority={},
    description_mapping={},
    suffix_fallback={".xlsx": "report"},
    path_template="uploads/{document_type}/{filename}",
    target_extraction=TargetExtractionConfig(
        strategy="broadcast",
        broadcast_target="alpha",
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_zip(entries: list[tuple[str, bytes]], encoding: str = "utf-8") -> bytes:
    """Build a zip in memory.

    entries: list of (arcname, data).
    encoding: filename encoding ('utf-8' sets flag_bits for UTF-8; 'gbk' does not).
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            info = zipfile.ZipInfo(name)
            if encoding == "utf-8":
                info.flag_bits |= 0x800  # UTF-8 flag
            else:
                info.flag_bits &= ~0x800  # clear UTF-8 flag — GBK territory
            zf.writestr(info, data)
    return buf.getvalue()


def _make_zip_gbk(entries: list[tuple[str, bytes]]) -> bytes:
    """Build a zip where filenames are GBK-encoded raw bytes stored without UTF-8 flag.

    Uses latin-1 as a byte-transparent carrier: GBK bytes are encoded as GBK then
    decoded as latin-1 so zipfile stores the raw bytes verbatim. The flag_bits UTF-8
    bit is cleared so the engine must detect encoding and fall back to GBK.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name_str, data in entries:
            # Encode name in GBK; use latin-1 as a transparent carrier into ZipInfo
            name_gbk_as_latin1 = name_str.encode("gbk").decode("latin-1")
            info = zipfile.ZipInfo(name_gbk_as_latin1)
            info.flag_bits &= ~0x800  # clear UTF-8 flag
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Cat 1 — ZIP 解码与安全（5）
# ---------------------------------------------------------------------------

def test_utf8_filename_decoded():
    """UTF-8 中文文件名 → item.filename 可读无乱码。"""
    name = "acme/月报.xlsx"
    zb = _make_zip([(name, b"data")])
    items, summary = classify_zip(zb, PROFILE_A)
    assert any("月报" in item.filename for item in items), (
        "Expected Chinese filename decoded without mojibake"
    )


def test_gbk_filename_decoded():
    """GBK 编码（flag_bits=0，无 UTF-8 flag）→ 仍可读。"""
    name = "acme/月报.xlsx"
    zb = _make_zip_gbk([(name, b"data")])
    items, summary = classify_zip(zb, PROFILE_A)
    assert any("月报" in item.filename for item in items), (
        "Expected GBK-encoded Chinese filename decoded correctly"
    )


def test_path_traversal_rejected():
    """entry 路径 ../evil.xlsx → severity='error', error_code='path_traversal'."""
    zb = _make_zip([("../evil.xlsx", b"pwn")])
    items, summary = classify_zip(zb, PROFILE_A)
    assert len(items) == 1
    item = items[0]
    assert item.severity == "error"
    assert item.error_code == "path_traversal"


def test_absolute_path_rejected():
    """entry 路径 /etc/evil.xlsx → severity='error', error_code='path_traversal'."""
    zb = _make_zip([("/etc/evil.xlsx", b"pwn")])
    items, summary = classify_zip(zb, PROFILE_A)
    assert len(items) == 1
    item = items[0]
    assert item.severity == "error"
    assert item.error_code == "path_traversal"


def test_directory_entry_skipped():
    """纯目录 entry（somedir/）→ 不在 items，不计入 total，不计入 ignored。"""
    zb = _make_zip([("somedir/", b""), ("acme/月报.xlsx", b"data")])
    items, summary = classify_zip(zb, PROFILE_A)
    # directory entry should not appear in items
    for item in items:
        assert not item.src_path.endswith("/"), "Directory entries must not appear in items"
    # total counts real files only — the directory entry must not inflate total
    assert summary.total == 1


# ---------------------------------------------------------------------------
# Cat 2 — Entry Filter（2）
# ---------------------------------------------------------------------------

def test_ignored_filename_not_in_items():
    """.DS_Store → items 为空，summary.ignored=1, total=1。"""
    zb = _make_zip([(".DS_Store", b"")])
    items, summary = classify_zip(zb, PROFILE_A)
    assert items == []
    assert summary.ignored == 1
    assert summary.total == 1


def test_mixed_ignored_and_ok_summary():
    """1 ignored + 1 ok → ignored=1, ok=1, total=2。"""
    zb = _make_zip([
        (".DS_Store", b""),
        ("acme/月报.xlsx", b"data"),
    ])
    items, summary = classify_zip(zb, PROFILE_A)
    assert summary.total == 2
    assert summary.ignored == 1
    assert summary.ok >= 1


# ---------------------------------------------------------------------------
# Cat 3 — Target Extraction Strategy（6）
# ---------------------------------------------------------------------------

def test_directory_strategy_folder_is_target():
    """acme/月报.xlsx，strategy=directory_or_filename → target_name_raw='acme'。"""
    zb = _make_zip([("acme/月报.xlsx", b"data")])
    items, _ = classify_zip(zb, PROFILE_A)
    assert len(items) == 1
    assert items[0].target_name_raw == "acme"


def test_directory_nested_only_first_level():
    """acme/2026/月报.xlsx → target_name_raw='acme'（只取第 1 层）。"""
    zb = _make_zip([("acme/2026/月报.xlsx", b"data")])
    items, _ = classify_zip(zb, PROFILE_A)
    assert len(items) == 1
    assert items[0].target_name_raw == "acme"


def test_broadcast_all_files_get_same_target():
    """Profile B（broadcast/alpha），两个文件 → 都 matched='alpha'。"""
    zb = _make_zip([
        ("report1.xlsx", b"data"),
        ("report2.xlsx", b"data"),
    ])
    items, _ = classify_zip(zb, PROFILE_B)
    assert len(items) == 2
    for item in items:
        assert item.target_name_matched == "alpha"


def test_fallback_flat_file_uses_filename_segment():
    """Flat root file falls back to filename segment target extraction."""
    zb = _make_zip([("月报-acme.xlsx", b"data")])
    items, _ = classify_zip(zb, PROFILE_A)
    assert len(items) == 1
    assert items[0].target_name_raw == "acme"


def test_mixed_zip_dir_and_flat_both_resolved():
    """acme/月报.xlsx（dir 路由）+ 季报-globex.xlsx（filename 兜底）→ 两条分别 matched。"""
    zb = _make_zip([
        ("acme/月报.xlsx", b"data"),
        ("季报-globex.xlsx", b"data"),
    ])
    items, _ = classify_zip(zb, PROFILE_A)
    assert len(items) == 2
    raws = {item.target_name_raw for item in items}
    assert "acme" in raws
    assert "globex" in raws


def test_flat_file_no_delimiter_produces_error():
    """Flat root file without delimiter fails target extraction."""
    zb = _make_zip([("月报.xlsx", b"data")])
    items, _ = classify_zip(zb, PROFILE_A)
    assert len(items) == 1
    assert items[0].severity == "error"
    assert items[0].error_code == "unknown_target"


# ---------------------------------------------------------------------------
# Cat 4 — Target Resolution（4）
# ---------------------------------------------------------------------------

def test_target_exact_match():
    """raw='acme'，targets 有 key='acme' → matched='acme', severity='ok'。"""
    zb = _make_zip([("acme/月报.xlsx", b"data")])
    items, _ = classify_zip(zb, PROFILE_A)
    assert len(items) == 1
    item = items[0]
    assert item.target_name_matched == "acme"
    assert item.severity == "ok"


def test_target_alias_resolved():
    """raw='ACME'，alias ACME→acme → matched='acme'。"""
    # Use a directory named "ACME" which is an alias for "acme"
    zb = _make_zip([("ACME/月报.xlsx", b"data")])
    items, _ = classify_zip(zb, PROFILE_A)
    assert len(items) == 1
    assert items[0].target_name_matched == "acme"


def test_target_strip_number_prefix():
    """raw='acme'，targets 有 key='12. acme'（strip_number_prefix=True）→ matched='12. acme'。"""
    profile = ProfileConfig(
        version="1.0",
        targets=[
            TargetConfig(key="12. acme", aliases=[], strip_number_prefix=True),
        ],
        document_types={"monthly": DocumentTypeConfig(category="reports")},
        suffix_priority={},
        description_mapping={"月报": "monthly"},
        suffix_fallback={".xlsx": "monthly"},
        path_template="{category}/{document_type}/{filename}",
        target_extraction=TargetExtractionConfig(strategy="directory_or_filename"),
    )
    # Directory named "acme" should strip number prefix from "12. acme" and match
    zb = _make_zip([("acme/月报.xlsx", b"data")])
    items, _ = classify_zip(zb, profile)
    assert len(items) == 1
    assert items[0].target_name_matched == "12. acme"


def test_unknown_target_error_item_kept():
    """Unknown target keeps an error item in classification results."""
    zb = _make_zip([("unknowncorp/月报.xlsx", b"data")])
    items, _ = classify_zip(zb, PROFILE_A)
    assert len(items) == 1
    item = items[0]
    assert item.severity == "error"
    assert item.target_name_matched is None


# ---------------------------------------------------------------------------
# Cat 5 — Classification Resolution（4）
# ---------------------------------------------------------------------------

def test_suffix_priority_beats_description():
    """.pdf 命中 suffix_priority → document_type 以 suffix_priority 为准，忽略描述。"""
    # "合同.pdf" — suffix_priority maps .pdf→contract; description also maps "合同"→contract
    # but what matters is that suffix_priority takes precedence even when description differs
    # Use a filename where description would map to "monthly" but .pdf maps to "contract"
    zb = _make_zip([("acme/月报.pdf", b"data")])
    items, _ = classify_zip(zb, PROFILE_A)
    assert len(items) == 1
    # suffix_priority: .pdf → contract; description would suggest monthly — suffix wins
    assert items[0].document_type == "contract"


def test_description_mapping_exact():
    """无 suffix_priority 命中；描述精确命中 description_mapping。"""
    # "月报.xlsx" — suffix_priority has .pdf→contract (no .xlsx entry); description "月报"→monthly
    # But suffix_priority has no .xlsx key, and suffix_fallback has .xlsx→monthly
    # Description priority needs a case where description differs from fallback.
    profile = ProfileConfig(
        version="1.0",
        targets=[TargetConfig(key="acme")],
        document_types={
            "monthly": DocumentTypeConfig(category="reports"),
            "contract": DocumentTypeConfig(category="legal"),
        },
        suffix_priority={},  # no suffix priority
        description_mapping={"合同": "contract"},
        suffix_fallback={".xlsx": "monthly"},  # fallback would give monthly
        path_template="{category}/{document_type}/{filename}",
        target_extraction=TargetExtractionConfig(strategy="directory_or_filename"),
    )
    # "合同.xlsx" — description "合同" → contract; suffix_fallback .xlsx → monthly
    # description should beat fallback
    zb = _make_zip([("acme/合同.xlsx", b"data")])
    items, _ = classify_zip(zb, profile)
    assert len(items) == 1
    assert items[0].document_type == "contract"


def test_suffix_fallback_when_no_match():
    """无任何命中 → document_type 从 suffix_fallback 取。"""
    profile = ProfileConfig(
        version="1.0",
        targets=[TargetConfig(key="acme")],
        document_types={"monthly": DocumentTypeConfig(category="reports")},
        suffix_priority={},
        description_mapping={},  # no description match
        suffix_fallback={".xlsx": "monthly"},
        path_template="{category}/{document_type}/{filename}",
        target_extraction=TargetExtractionConfig(strategy="directory_or_filename"),
    )
    zb = _make_zip([("acme/数据.xlsx", b"data")])
    items, _ = classify_zip(zb, profile)
    assert len(items) == 1
    assert items[0].document_type == "monthly"


def test_fuzzy_description_enabled_vs_disabled():
    """近似描述（如"月份报"接近"月报"）：fuzzy 开 → 命中；fuzzy 关 → fallback。"""
    base_kwargs = dict(
        version="1.0",
        targets=[TargetConfig(key="acme")],
        document_types={
            "monthly": DocumentTypeConfig(category="reports"),
            "other": DocumentTypeConfig(category="misc"),
        },
        suffix_priority={},
        description_mapping={"月报": "monthly"},
        suffix_fallback={".xlsx": "other"},
        path_template="{category}/{document_type}/{filename}",
        target_extraction=TargetExtractionConfig(strategy="directory_or_filename"),
    )

    profile_fuzzy_on = ProfileConfig(
        **base_kwargs,
        matching_config=MatchingConfig(enable_fuzzy_match=True, description_fuzzy_threshold=60),
    )
    profile_fuzzy_off = ProfileConfig(
        **base_kwargs,
        matching_config=MatchingConfig(enable_fuzzy_match=False),
    )

    zb = _make_zip([("acme/月份报.xlsx", b"data")])

    items_on, _ = classify_zip(zb, profile_fuzzy_on)
    items_off, _ = classify_zip(zb, profile_fuzzy_off)

    assert len(items_on) == 1
    assert len(items_off) == 1
    # fuzzy on → description fuzzy match → monthly
    assert items_on[0].document_type == "monthly"
    # fuzzy off → no description match → fallback → other
    assert items_off[0].document_type == "other"


# ---------------------------------------------------------------------------
# Cat 6 — 路径渲染（2）
# ---------------------------------------------------------------------------

def test_path_template_renders_correctly():
    """{category}/{document_type}/{filename} → 'reports/monthly/月报-acme.xlsx'。"""
    zb = _make_zip([("acme/月报-acme.xlsx", b"data")])
    items, _ = classify_zip(zb, PROFILE_A)
    assert len(items) == 1
    item = items[0]
    assert item.severity != "error" or item.error_code != "path_render_error"
    assert item.dst_path == "reports/monthly/月报-acme.xlsx"


def test_path_template_dotdot_blocked():
    """渲染结果含 .. → severity='error', error_code='path_render_error'。"""
    # Craft a profile whose path_template can produce '..' in output
    profile = ProfileConfig(
        version="1.0",
        targets=[TargetConfig(key="acme")],
        document_types={"monthly": DocumentTypeConfig(category="..")},
        suffix_priority={},
        description_mapping={"月报": "monthly"},
        suffix_fallback={".xlsx": "monthly"},
        path_template="{category}/{document_type}/{filename}",
        target_extraction=TargetExtractionConfig(strategy="directory_or_filename"),
    )
    zb = _make_zip([("acme/月报.xlsx", b"data")])
    items, _ = classify_zip(zb, profile)
    assert len(items) == 1
    item = items[0]
    assert item.severity == "error"
    assert item.error_code == "path_render_error"


# ---------------------------------------------------------------------------
# Cat 7 — Summary（2）
# ---------------------------------------------------------------------------

def test_empty_zip_summary_all_zeros():
    """空 zip → items=[], 全字段=0, has_blocking_errors=False。"""
    zb = _make_zip([])
    items, summary = classify_zip(zb, PROFILE_A)
    assert items == []
    assert summary.total == 0
    assert summary.ok == 0
    assert summary.warning == 0
    assert summary.error == 0
    assert summary.ignored == 0
    assert summary.has_blocking_errors is False


def test_has_blocking_errors_reflects_error_count():
    """有 error item → has_blocking_errors=True；全 ok → False。"""
    # All-ok zip: a known-good file in a known target
    zb_ok = _make_zip([("acme/月报.xlsx", b"data")])
    _, summary_ok = classify_zip(zb_ok, PROFILE_A)
    assert summary_ok.has_blocking_errors is False

    # Error zip: path traversal triggers error
    zb_err = _make_zip([("../evil.xlsx", b"pwn")])
    _, summary_err = classify_zip(zb_err, PROFILE_A)
    assert summary_err.has_blocking_errors is True


# ---------------------------------------------------------------------------
# Cat 8 — Profile 隔离（1）
# ---------------------------------------------------------------------------

def test_same_zip_different_profile_different_result():
    """同 zip_bytes 分别用 Profile A / Profile B → dst_path 不同。"""
    zb = _make_zip([("月报.xlsx", b"data")])
    items_a, _ = classify_zip(zb, PROFILE_A)
    items_b, _ = classify_zip(zb, PROFILE_B)

    # Both should produce an item (even if error on A due to no target found)
    assert len(items_a) == 1
    assert len(items_b) == 1

    # dst_path should differ between profiles
    assert items_a[0].dst_path != items_b[0].dst_path, (
        "Same zip with different profiles must produce different dst_path"
    )
