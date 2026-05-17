from __future__ import annotations

from app.jobs.cleanup_staging_sources import build_parser


def test_cleanup_staging_sources_job_parses_options():
    args = build_parser().parse_args([
        "--retention-days",
        "3",
        "--bucket-name",
        "auto-upload-staging",
    ])

    assert args.retention_days == 3
    assert args.bucket_name == "auto-upload-staging"
