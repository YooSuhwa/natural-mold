"""Company export jobs (plan S04 "내보내기", 9.7; I-07).

``start_export`` records a queued job and the ``tenant.export`` audit in one
transaction (one queued/running job per company: ``409 tenant_export_in_progress``);
the router then runs ``run_export`` after the response. The job reads the company
in a platform session (an operator job spans the company and its members' accounts),
writes ``data_root/tenant_exports/<tenant>/<job>.zip`` and keeps the link for
``tenant_export_ttl_days`` (7). A job that was interrupted stays ``running``; it only
blocks a new export until an operator fails it (follow-up: restart recovery).
"""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.config import settings
from app.database import async_session, platform_session
from app.exceptions import AppError
from app.models.organization import Tenant
from app.models.tenant_export import TenantExport
from app.schemas.platform_tenant_data import TenantExportJob
from app.services.platform.tenant_command import TenantCommand, lock_tenant, record
from app.services.platform.tenant_export_archive import write_archive
from app.services.platform.tenant_files import local_path
from app.services.tenants.versioning import utc_timestamp

logger = logging.getLogger(__name__)
EXPORT_ROOT = "tenant_exports"
ACTIVE = ("queued", "running")
LIST_LIMIT = 50


def storage_path(tenant_id: UUID, export_id: UUID) -> str:
    return f"{EXPORT_ROOT}/{tenant_id}/{export_id}.zip"


def downloadable(row: TenantExport, now: datetime) -> bool:
    return (
        row.status == "succeeded"
        and row.storage_path is not None
        and row.expires_at is not None
        and utc_timestamp(row.expires_at) > now
    )


def job_view(row: TenantExport, now: datetime) -> TenantExportJob:
    return TenantExportJob(
        id=row.id,
        tenant_id=row.tenant_id,
        status=row.status,  # type: ignore[arg-type] - the CHECK constraint keeps the literal
        progress=row.progress,
        error_code=row.error_code,
        size_bytes=row.size_bytes,
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        expires_at=row.expires_at,
        download_available=downloadable(row, now),
    )


async def start_export(command: TenantCommand) -> UUID:
    db = command.db
    try:
        tenant = await lock_tenant(db, command.tenant_id)
        active = await db.scalar(
            select(TenantExport.id)
            .where(TenantExport.tenant_id == tenant.id, TenantExport.status.in_(ACTIVE))
            .limit(1)
        )
        if active is not None:
            raise authorization_error("tenant_export_in_progress", {"job_id": str(active)})
        actor = command.actor
        row = TenantExport(
            tenant_id=tenant.id,
            requested_by=None if actor is None else actor.id,
            status="queued",
            progress=0,
        )
        db.add(row)
        await db.flush()
        export_id = row.id
        await record(command, tenant, audit_actions.TENANT_EXPORT, {"job_id": str(export_id)})
        await db.commit()
    except (AppError, SQLAlchemyError):
        await db.rollback()
        raise
    return export_id


async def list_exports(db: AsyncSession, tenant_id: UUID) -> list[TenantExport]:
    if await db.scalar(select(Tenant.id).where(Tenant.id == tenant_id)) is None:
        raise authorization_error("not_found")
    rows = await db.scalars(
        select(TenantExport)
        .where(TenantExport.tenant_id == tenant_id)
        .order_by(TenantExport.created_at.desc(), TenantExport.id.desc())
        .limit(LIST_LIMIT)
    )
    return list(rows)


async def export_file(db: AsyncSession, tenant_id: UUID, export_id: UUID) -> Path:
    """The finished archive; a missing, foreign, unfinished or expired job is 404."""
    row = await db.scalar(
        select(TenantExport).where(
            TenantExport.id == export_id, TenantExport.tenant_id == tenant_id
        )
    )
    now = datetime.now(UTC)
    path = None if row is None or not downloadable(row, now) else local_path(row.storage_path or "")
    if path is None or not path.is_file():
        raise authorization_error("not_found")
    return path


async def _set(db: AsyncSession, export_id: UUID, **values: object) -> None:
    row = await db.get(TenantExport, export_id, populate_existing=True)
    if row is not None:
        for name, value in values.items():
            setattr(row, name, value)
        await db.commit()


async def run_export(export_id: UUID, *, now: datetime | None = None) -> None:
    """Build one queued job's archive; failures end the job as ``failed``."""
    async with platform_session("tenant_lifecycle.export", maker=async_session) as db:
        row = await db.get(TenantExport, export_id)
        if row is None or row.status != "queued":
            return
        tenant_id, relative = row.tenant_id, storage_path(row.tenant_id, export_id)
        started = now or datetime.now(UTC)
        await _set(db, export_id, status="running", started_at=started, progress=1)

        async def progress(value: int) -> None:
            await _set(db, export_id, progress=max(1, value))

        try:
            target = local_path(relative)
            if target is None:
                raise ValueError("export path escapes data_root")
            summary = await write_archive(db, tenant_id, target, progress)
        except Exception:  # noqa: BLE001 - the job row carries the failure
            logger.exception("tenant export %s failed", export_id)
            await db.rollback()
            done = datetime.now(UTC)
            await _set(
                db, export_id, status="failed", error_code="export_failed", completed_at=done
            )
            return
        done = datetime.now(UTC)
        await _set(
            db,
            export_id,
            status="succeeded",
            progress=100,
            storage_path=relative,
            size_bytes=summary.size_bytes,
            completed_at=done,
            expires_at=done + timedelta(days=settings.tenant_export_ttl_days),
        )
