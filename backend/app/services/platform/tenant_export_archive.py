"""Write one company's export archive (plan S04 "내보내기"; I-07).

Zip layout: ``manifest.json`` (company, time, row counts, redacted columns),
``tables/<table>.jsonl`` (one JSON object per row, every company-scoped table from
``tenant_data_scope``; the company row itself and its members' user rows included) and
``files/...`` (attachments, artifact trees, agent directories, skill packages).
Secrets are excluded, not re-encrypted (``tenant_export_redaction``); marketplace ACL
rows naming users outside the company are left out. Rows are streamed from the
database ``BATCH`` at a time straight into the zip entry, so memory stays bounded. The
archive is written to ``<target>.partial`` and renamed when complete.
"""

import json
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import IO
from uuid import UUID

from anyio.to_thread import run_sync
from sqlalchemy import ColumnElement, Table, and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import TenantMember
from app.services.platform.tenant_data_scope import company_tables, metadata, secret_columns
from app.services.platform.tenant_export_redaction import USER_COLUMNS, clean_row
from app.services.platform.tenant_files import CompanyFiles, company_files, export_entries

Progress = Callable[[int], Awaitable[None]]
FORMAT_VERSION = 1
BATCH = 500
Source = tuple[Table, ColumnElement[bool]]


@dataclass(frozen=True, slots=True)
class ArchiveSummary:
    path: Path
    size_bytes: int
    row_counts: dict[str, int]
    file_count: int


def _json(value: object) -> object:
    if isinstance(value, UUID | Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _sources(tenant_id: UUID) -> list[Source]:
    tables = metadata().tables
    tenants, users, acl = tables["tenants"], tables["users"], tables["marketplace_item_acl"]
    members = select(TenantMember.user_id).where(TenantMember.tenant_id == tenant_id)
    found: list[Source] = [(tenants, tenants.c.id == tenant_id), (users, users.c.id.in_(members))]
    for item in company_tables(tenant_id):
        where = item.where
        if item.table is acl:  # never another company's user ids
            where = and_(where, acl.c.user_id.in_(members))
        found.append((item.table, where))
    return found


async def _stream_table(db: AsyncSession, handle: IO[bytes], source: Source) -> int:
    table, where = source
    result = await db.stream(select(table).where(where).execution_options(yield_per=BATCH))
    count = 0
    async for batch in result.mappings().partitions(BATCH):
        lines = [
            json.dumps(
                clean_row(table, dict(row)), default=_json, sort_keys=True, ensure_ascii=False
            )
            for row in batch
        ]
        count += len(lines)
        await run_sync(handle.write, ("\n".join(lines) + "\n").encode())
    return count


async def _write(
    db: AsyncSession,
    partial: Path,
    sources: list[Source],
    files: CompanyFiles,
    tenant_id: UUID,
    progress: Progress | None,
) -> tuple[dict[str, int], int]:
    counts: dict[str, int] = {}
    redacted: dict[str, list[str]] = {}
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, source in enumerate(sources, start=1):
            table = source[0]
            name = f"tables/{table.name}.jsonl"
            with archive.open(name, "w", force_zip64=True) as handle:
                counts[table.name] = await _stream_table(db, handle, source)
            hidden = set(secret_columns(table))
            if table.name == "users":
                hidden |= {column.name for column in table.c if column.name not in USER_COLUMNS}
            if hidden:
                redacted[table.name] = sorted(hidden)
            if progress is not None:
                await progress(min(90, index * 90 // len(sources)))
        entries = await run_sync(lambda: list(export_entries(files)))

        def copy_files() -> None:
            for entry, path in entries:
                archive.write(path, entry)

        await run_sync(copy_files)
        manifest = {
            "format_version": FORMAT_VERSION,
            "tenant_id": str(tenant_id),
            "exported_at": datetime.now(UTC).isoformat(),
            "row_counts": counts,
            "redacted_columns": redacted,
            "file_count": len(entries),
        }
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
    return counts, len(entries)


def _publish(partial: Path, target: Path) -> int:
    partial.replace(target)
    return target.stat().st_size


async def write_archive(
    db: AsyncSession, tenant_id: UUID, target: Path, progress: Progress | None = None
) -> ArchiveSummary:
    files = await company_files(db, tenant_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    try:
        counts, entries = await _write(db, partial, _sources(tenant_id), files, tenant_id, progress)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    size = await run_sync(_publish, partial, target)
    return ArchiveSummary(target, size, counts, entries)
