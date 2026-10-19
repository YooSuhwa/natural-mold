"""Operator CLI for company export and deletion (plan S04; I-07).

    python -m app.services.platform.tenant_data_cli export --tenant ID --out DIR
    python -m app.services.platform.tenant_data_cli schedule-deletion --tenant ID --slug SLUG
    python -m app.services.platform.tenant_data_cli cancel-deletion --tenant ID
    python -m app.services.platform.tenant_data_cli purge [--tenant ID]

``export`` writes ``<DIR>/tenant-<ID>.zip`` directly (no job row, same archive as the
API). ``purge`` runs the leader job body once (every due company, or one company if it
is due); it never purges a company before its ``deletion_due_at``. Commands act as the
operator shell (audit ``actor_type=system``, ``actor_label=operator_cli``). Exit codes:
0 success, 1 refused (not found, wrong state, not due), 2 usage errors (argparse).
"""

import argparse
import json
import sys
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from uuid import UUID

import anyio
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import platform_session
from app.exceptions import AppError
from app.models.organization import Tenant
from app.services.platform.tenant_command import TenantCommand
from app.services.platform.tenant_deletion import cancel_tenant_deletion, schedule_tenant_deletion
from app.services.platform.tenant_export_archive import write_archive
from app.services.platform.tenant_purge import purge_tenant
from app.services.platform.tenant_purge_job import purge_sessions, run_tenant_purge_job
from app.services.tenants.versioning import etag

Maker = Callable[[], AsyncSession] | None


def _session(maker: Maker) -> AsyncSession:
    if maker is None:
        return platform_session("tenant_lifecycle.cli")
    return platform_session("tenant_lifecycle.cli", maker=maker)


async def _versioned(
    maker: Maker, tenant_id: UUID, act: Callable[[TenantCommand], Awaitable[None]]
) -> dict[str, object]:
    async with _session(maker) as db:
        tenant = await db.get(Tenant, tenant_id)
        if tenant is None:
            raise AppError(code="not_found", message="company not found", status=404)
        await act(TenantCommand(db, tenant_id, None, etag(tenant.updated_at)))
        fresh = await db.get(Tenant, tenant_id, populate_existing=True)
        if fresh is None:
            raise AppError(code="not_found", message="company not found", status=404)
        due = fresh.deletion_due_at
        return {
            "tenant_id": str(tenant_id),
            "status": fresh.status,
            "deletion_due_at": None if due is None else due.isoformat(),
        }


async def _export(maker: Maker, tenant_id: UUID, out: Path) -> dict[str, object]:
    async with _session(maker) as db:
        if await db.get(Tenant, tenant_id) is None:
            raise AppError(code="not_found", message="company not found", status=404)
        summary = await write_archive(db, tenant_id, out / f"tenant-{tenant_id}.zip")
    return {
        "path": str(summary.path),
        "size_bytes": summary.size_bytes,
        "rows": summary.row_counts,
        "files": summary.file_count,
    }


async def _purge(maker: Maker, tenant_id: UUID | None) -> dict[str, object]:
    if tenant_id is None:
        results = await run_tenant_purge_job(maker=maker)
    else:
        sessions = purge_sessions(maker)
        one = await purge_tenant(sessions, tenant_id)
        if one is None:
            raise AppError(
                code="tenant_status_conflict", message="company deletion is not due", status=409
            )
        results = [one]
    return {"purged": [{"tenant_id": str(r.tenant_id), "rows": r.rows} for r in results]}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tenant_data_cli")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--tenant", type=UUID, required=True)
    export.add_argument("--out", type=Path, required=True)
    schedule = commands.add_parser("schedule-deletion")
    schedule.add_argument("--tenant", type=UUID, required=True)
    schedule.add_argument("--slug", required=True)
    schedule.add_argument("--reason")
    cancel = commands.add_parser("cancel-deletion")
    cancel.add_argument("--tenant", type=UUID, required=True)
    cancel.add_argument("--reason")
    purge = commands.add_parser("purge")
    purge.add_argument("--tenant", type=UUID)
    return parser


async def run(argv: Sequence[str], maker: Maker = None) -> tuple[int, dict[str, object]]:
    args = _parser().parse_args(argv)
    try:
        match args.command:
            case "export":
                return 0, await _export(maker, args.tenant, args.out)
            case "schedule-deletion":
                act = lambda c: schedule_tenant_deletion(c, args.slug, args.reason)  # noqa: E731
                return 0, await _versioned(maker, args.tenant, act)
            case "cancel-deletion":
                act = lambda c: cancel_tenant_deletion(c, args.reason)  # noqa: E731
                return 0, await _versioned(maker, args.tenant, act)
            case _:
                return 0, await _purge(maker, args.tenant)
    except AppError as error:
        return 1, {"error": error.code, "message": error.message}


def main(argv: Sequence[str] | None = None) -> int:
    code, output = anyio.run(run, list(sys.argv[1:] if argv is None else argv))
    print(json.dumps(output, indent=2, sort_keys=True, default=str))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
