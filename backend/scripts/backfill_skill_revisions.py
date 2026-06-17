# /// script
# requires-python = ">=3.12"
# ///
# --- How to run ---
# Dry run:
#   uv run python scripts/backfill_skill_revisions.py --dry-run
# Apply:
#   uv run python scripts/backfill_skill_revisions.py --batch-size 100

from __future__ import annotations

import argparse
import logging

import anyio

from app.database import async_session
from app.services import skill_revision_retention

logger = logging.getLogger(__name__)


async def _run(args: argparse.Namespace) -> None:
    async with async_session() as db:
        if args.dry_run:
            missing = await skill_revision_retention.count_skills_missing_revisions(db)
            logger.info("would backfill %d skill(s) with no revisions", missing)
            return

        total = 0
        batches = 0
        while True:
            count = await skill_revision_retention.backfill_missing_revisions(
                db,
                batch_size=args.batch_size,
            )
            total += count
            batches += 1
            await db.commit()
            logger.info("backfilled %d skill(s) in batch %d", count, batches)
            if count < args.batch_size:
                break
            if args.max_batches is not None and batches >= args.max_batches:
                break
        logger.info("backfill complete: %d skill(s)", total)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill baseline SkillRevision rows for legacy skills.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Count targets without writing.")
    parser.add_argument("--batch-size", type=int, default=100, help="Skills to process per batch.")
    parser.add_argument(
        "--max-batches",
        type=int,
        default=None,
        help="Optional cap for long-running maintenance windows.",
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    anyio.run(_run, _build_parser().parse_args())


if __name__ == "__main__":
    main()
