"""k-skill sync CLI — super_user operator tool (Spec §5.9).

Usage::

    uv run python -m app.scripts.sync_k_skill --ref main
    uv run python -m app.scripts.sync_k_skill --dry-run --only srt-booking
    uv run python -m app.scripts.sync_k_skill --keep-deprecated

The script is **not** wired into any web route — Spec §0.1 keeps this
behind operator hands so an unattended bot can't push code into the
``is_system=True`` namespace. The matching admin endpoint
(``POST /api/marketplace/admin/k-skill/sync``) only returns the most
recent sync log entry; running the sync is an out-of-band action.

Exit codes:

* ``0`` — sync ran (possibly with per-skill failures; check the JSON
  report on stdout for the ``failed`` counter).
* ``1`` — git checkout / setup failed before any skill was processed.
* ``2`` — invalid CLI arguments.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

from app.config import settings
from app.database import async_session
from app.marketplace.k_skill_importer import SyncReport, run_sync

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Git plumbing
# ---------------------------------------------------------------------------


def _git_resolve_head(repo_dir: Path) -> str:
    """Return the current ``HEAD`` commit SHA. Used as ``source_commit``
    on every imported version."""

    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 — git via PATH
            cwd=repo_dir,
            text=True,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def ensure_upstream_checkout(*, url: str, ref: str, sync_dir: Path) -> tuple[bool, str]:
    """Clone or update the local mirror of the upstream repo.

    Returns ``(ok, commit_sha)``. ``ok=False`` means the operator must
    intervene (network failure, dirty working tree, etc.) — the caller
    aborts with exit code 1 in that case.

    Design choices:

    * Shallow ``--depth=1`` clone — we don't need history for sync.
    * Re-clone path nuked when ref switch is requested. Easier than
      reasoning about partial fetches on the operator machine.
    * Uses system ``git`` because libgit2 isn't a project dependency.
    """

    if not shutil.which("git"):
        return False, ""

    sync_dir.parent.mkdir(parents=True, exist_ok=True)

    if not (sync_dir / ".git").exists():
        if sync_dir.exists():
            shutil.rmtree(sync_dir, ignore_errors=True)
        try:
            subprocess.check_call(  # noqa: S603 — operator CLI; url from settings, ref from argv
                [  # noqa: S607 — git via PATH
                    "git",
                    "clone",
                    "--depth=1",
                    "--branch",
                    ref,
                    url,
                    str(sync_dir),
                ]
            )
        except subprocess.CalledProcessError:
            return False, ""
        return True, _git_resolve_head(sync_dir)

    # Existing checkout — fetch the requested ref, hard-reset to it.
    try:
        subprocess.check_call(["git", "fetch", "origin", ref], cwd=sync_dir)  # noqa: S603, S607 — operator CLI; git via PATH
        subprocess.check_call(  # noqa: S603 — operator CLI; ref from argv
            ["git", "checkout", "-B", ref, f"origin/{ref}"],  # noqa: S607 — git via PATH
            cwd=sync_dir,
        )
        subprocess.check_call(["git", "reset", "--hard", f"origin/{ref}"], cwd=sync_dir)  # noqa: S603, S607 — operator CLI; git via PATH
    except subprocess.CalledProcessError:
        return False, ""
    return True, _git_resolve_head(sync_dir)


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sync_k_skill",
        description="Import / refresh skills from the upstream k-skill repo.",
    )
    parser.add_argument(
        "--ref",
        default=settings.k_skill_upstream_ref,
        help="Upstream branch/tag/commit to sync (default: %(default)s).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Walk + validate + scan but don't write to DB or storage.",
    )
    parser.add_argument(
        "--only",
        action="append",
        metavar="NAME",
        help="Restrict the run to specific upstream skill name(s). Repeat for multiple.",
    )
    parser.add_argument(
        "--keep-deprecated",
        action="store_true",
        default=True,
        help=(
            "Leave previously-imported skills that disappeared upstream "
            "in their current state. Default true — pass --no-keep-deprecated "
            "to actively mark them deprecated."
        ),
    )
    parser.add_argument(
        "--no-keep-deprecated",
        dest="keep_deprecated",
        action="store_false",
    )
    parser.add_argument(
        "--skip-git",
        action="store_true",
        help=(
            "Skip the git fetch step — use the already-on-disk checkout "
            "at ``settings.k_skill_sync_dir``. Useful for offline replay "
            "of a previously-fetched commit."
        ),
    )
    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _resolve_path(value: str) -> Path:
    """Sync helper for ``Path.resolve`` — kept out of the async fn to
    satisfy ASYNC240 (no pathlib I/O methods inside async)."""

    return Path(value).resolve()


async def _run(args: argparse.Namespace) -> SyncReport:
    sync_dir = await asyncio.to_thread(_resolve_path, settings.k_skill_sync_dir)
    # ADR-018 — k-skill snapshots land under ``<data_root>/marketplace/k-skill``
    # to match the relative form ``marketplace/k-skill/<vid>`` written into
    # ``marketplace_versions.storage_path``.
    storage_dir = await asyncio.to_thread(
        _resolve_path, str(Path(settings.data_root) / "marketplace" / "k-skill")
    )

    if args.skip_git:
        commit_sha = _git_resolve_head(sync_dir)
        ok = sync_dir.exists()
    else:
        ok, commit_sha = ensure_upstream_checkout(
            url=settings.k_skill_upstream_url,
            ref=args.ref,
            sync_dir=sync_dir,
        )

    if not ok:
        logger.error(
            "k-skill checkout failed: dir=%s ref=%s — aborting",
            sync_dir,
            args.ref,
        )
        sys.exit(1)

    async with async_session() as db:
        report = await run_sync(
            repo_dir=sync_dir,
            builtin_storage_dir=storage_dir,
            ref=args.ref,
            commit_sha=commit_sha,
            db=db,
            dry_run=args.dry_run,
            only=args.only,
            keep_deprecated=args.keep_deprecated,
        )
        if args.dry_run:
            await db.rollback()
        else:
            await db.commit()
    return report


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = _build_parser()
    args = parser.parse_args()
    try:
        report = asyncio.run(_run(args))
    except KeyboardInterrupt:
        sys.exit(130)
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
