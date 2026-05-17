from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import timedelta

from app.core.db import async_session_maker
from app.services.staging_cleanup import cleanup_staging_sources


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Delete expired staging source objects.")
    parser.add_argument(
        "--retention-days",
        type=int,
        default=7,
        help="minimum age in days before a terminal task's staged source can be deleted",
    )
    parser.add_argument(
        "--bucket-name",
        default=None,
        help="optional staging bucket filter",
    )
    return parser


async def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    async with async_session_maker() as session:
        summary = await cleanup_staging_sources(
            session,
            retention=timedelta(days=args.retention_days),
            bucket_name=args.bucket_name,
        )
        await session.commit()
    print(json.dumps(asdict(summary), sort_keys=True))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
