"""Reassign data owned by the legacy mock user to a real authenticated user.

ADR-016 §6 + Phase 8 — when an existing PoC database (where everything was
written under the canonical mock UUID ``00000000-0000-0000-0000-000000000001``)
gets its first real user via ``/api/auth/register``, an operator runs this
script once to migrate the mock user's agents/credentials/tools/etc to the
new account.

Usage::

    uv run python scripts/migrate_mock_to_real_user.py \\
        --target-user-id <UUID> [--source-user-id <UUID>] \\
        [--dry-run] [--delete-source]

* ``--target-user-id``: the UUID of the real user that takes ownership.
  (typically the freshly-registered super_user — check ``users`` table.)
* ``--source-user-id`` (default: ``00000000-0000-0000-0000-000000000001``):
  the legacy mock user. Override only if your install picked a different
  bootstrap UUID.
* ``--dry-run``: log what *would* change. No writes.
* ``--delete-source``: after a successful migration, ``DELETE FROM users
  WHERE id=:source``. CASCADE will clean up anything still pointing to it.
  Off by default — operators usually inspect first.

Idempotent: re-running after a successful migration is a no-op (UPDATEs
match zero rows). The script never moves system rows (``is_system=true``)
because system credentials/tools are operator-managed and live with
``user_id IS NULL`` per m36.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.user import User
from app.services.user_service import cleanup_user_resources

logger = logging.getLogger(__name__)

# Legacy mock UUID — historically baked into ``app.config.settings.mock_user_id``
# before S3 ripped that setting out. Any pre-multiuser install will have this
# row as the owner of every agent/tool/credential/etc.
DEFAULT_SOURCE_UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")

# (table, owner_column, extra_filter) — extra_filter excludes system rows
# (``is_system=true``) which must stay with ``user_id IS NULL`` per ADR-016.
_REASSIGN_TABLES: list[tuple[str, str, str]] = [
    ("agents", "user_id", ""),
    ("builder_sessions", "user_id", ""),
    ("agent_triggers", "user_id", ""),
    ("tools", "user_id", " AND is_system = false"),
    ("credentials", "user_id", " AND is_system = false"),
    ("daily_spend_user", "user_id", ""),
]


@dataclass
class MigrationStats:
    table: str
    rows_changed: int


async def _fetch_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()


async def _validate(
    db: AsyncSession,
    *,
    source: uuid.UUID,
    target: uuid.UUID,
) -> None:
    if source == target:
        raise SystemExit("source and target must differ")

    target_row = await _fetch_user(db, target)
    if target_row is None:
        raise SystemExit(
            f"target user {target} not found — register the real user first"
        )
    if not target_row.is_super_user:
        # Warning only — operators may legitimately migrate to a non-admin in
        # weird recovery cases. Still highlight loudly.
        logger.warning(
            "target user %s is NOT is_super_user — proceed only if intentional",
            target,
        )

    source_row = await _fetch_user(db, source)
    if source_row is None:
        logger.info(
            "source user %s does not exist — nothing to migrate. Exiting.", source
        )
        raise SystemExit(0)


async def _reassign(
    db: AsyncSession,
    *,
    source: uuid.UUID,
    target: uuid.UUID,
    dry_run: bool,
) -> list[MigrationStats]:
    stats: list[MigrationStats] = []
    for table, column, extra in _REASSIGN_TABLES:
        count_sql = (
            f"SELECT COUNT(*) FROM {table} WHERE {column} = :source{extra}"
        )
        n = (
            await db.execute(text(count_sql).bindparams(source=source))
        ).scalar_one()
        stats.append(MigrationStats(table=table, rows_changed=int(n)))

        if dry_run or n == 0:
            continue

        update_sql = (
            f"UPDATE {table} SET {column} = :target "
            f"WHERE {column} = :source{extra}"
        )
        await db.execute(
            text(update_sql).bindparams(source=source, target=target)
        )
    return stats


async def _delete_source(db: AsyncSession, source: uuid.UUID) -> int:
    """Final ``DELETE FROM users`` — runs only when ``--delete-source``."""

    result = await db.execute(
        text("DELETE FROM users WHERE id = :id").bindparams(id=source)
    )
    return int(result.rowcount or 0)


async def _run(args: argparse.Namespace) -> None:
    target = uuid.UUID(args.target_user_id)
    source = uuid.UUID(args.source_user_id)

    logger.info(
        "migrate_mock_to_real_user: source=%s target=%s dry_run=%s delete_source=%s",
        source,
        target,
        args.dry_run,
        args.delete_source,
    )

    async with async_session() as db:
        # Single autobegin transaction — every UPDATE / DELETE shares the
        # same TX so a failure mid-way rolls back cleanly. We commit() once
        # at the end (skipped when --dry-run).
        await _validate(db, source=source, target=target)

        stats = await _reassign(
            db,
            source=source,
            target=target,
            dry_run=args.dry_run,
        )

        for s in stats:
            action = "would update" if args.dry_run else "updated"
            logger.info("%s %d row(s) in %s", action, s.rows_changed, s.table)

        if args.delete_source and not args.dry_run:
            # Defensive cleanup before User row removal. Reassignment moves
            # agents/credentials/etc to the target user, so transitively-owned
            # conversations follow automatically — but any artifact still tied
            # directly to ``source`` (stray refresh tokens from a leaked
            # session, LangGraph threads from edge-case schemas) must be torn
            # down explicitly. ``cleanup_user_resources`` is idempotent and
            # shares this transaction (no commit inside).
            await cleanup_user_resources(db, source)
            deleted = await _delete_source(db, source)
            logger.info("deleted %d row(s) from users", deleted)
        elif args.delete_source and args.dry_run:
            logger.info(
                "would run cleanup_user_resources(%s) + delete source user row from users",
                source,
            )

        if args.dry_run:
            await db.rollback()
        else:
            await db.commit()

    total = sum(s.rows_changed for s in stats)
    logger.info(
        "%s — %d row(s) across %d table(s)%s",
        "DRY RUN COMPLETE" if args.dry_run else "MIGRATION COMPLETE",
        total,
        len(stats),
        " + source user removed" if (args.delete_source and not args.dry_run) else "",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Reassign rows owned by the legacy mock user to a real "
            "authenticated user. ADR-016 §6."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Always run --dry-run first. Run --delete-source only after "
            "verifying the dry-run output matches what you expect."
        ),
    )
    parser.add_argument(
        "--target-user-id",
        required=True,
        help="UUID of the real user that takes ownership.",
    )
    parser.add_argument(
        "--source-user-id",
        default=str(DEFAULT_SOURCE_UUID),
        help=(
            "UUID of the legacy mock user "
            f"(default: {DEFAULT_SOURCE_UUID})."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would change without writing.",
    )
    parser.add_argument(
        "--delete-source",
        action="store_true",
        help=(
            "After a successful migration, DELETE the source user row "
            "(CASCADE cleans any leftovers)."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    args = _build_parser().parse_args(argv)
    try:
        asyncio.run(_run(args))
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        logger.exception("migration failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
