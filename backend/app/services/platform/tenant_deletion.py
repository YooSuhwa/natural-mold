"""Schedule and cancel a company's permanent deletion (plan S04 "삭제 예약"; I-07).

Schedule: the operator types the company slug; ``tenants.status`` becomes
``deletion_scheduled`` with ``deletion_due_at = now + tenant_deletion_grace_days``
(30). Every company request is then refused by the existing status checks
(``ensure_tenant_active``, ``resolve_org_context``, the execution prefix: anything
but ``active`` is denied), sessions stay as they are until the purge. Cancel returns
the company to ``suspended`` (S04: "예약 취소로 정지 상태로 되돌림"), also after the due
time as long as the purge has not run: the purge takes the same company row lock for its
whole transaction and re-checks the status, so a cancel either wins before it or finds
the company gone (404).

Order (as suspend/resume): company row lock -> ``If-Match`` -> repeated target state
is an idempotent no-op (same version, no audit) -> allowed source state -> write +
platform audit (``tenant.deletion_schedule`` / ``tenant.deletion_cancel``) in one
transaction.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.config import settings
from app.exceptions import AppError
from app.models.organization import Tenant
from app.schemas.platform_tenant_data import TenantDeletionState
from app.services.platform.tenant_command import TenantCommand, lock_tenant, record
from app.services.platform.tenant_directory import get_platform_tenant
from app.services.tenants.versioning import bump_timestamp, require_current_version, utc_timestamp

SCHEDULABLE = ("active", "suspended")


def due_passed(tenant: Tenant, now: datetime) -> bool:
    due = tenant.deletion_due_at
    return due is not None and utc_timestamp(due) <= now


async def deletion_state(db: AsyncSession, tenant_id: UUID) -> TenantDeletionState:
    detail = await get_platform_tenant(db, tenant_id)
    due = await db.scalar(select(Tenant.deletion_due_at).where(Tenant.id == tenant_id))
    return TenantDeletionState(tenant=detail, deletion_due_at=due)


async def schedule_tenant_deletion(
    command: TenantCommand,
    confirm_slug: str,
    reason: str | None = None,
    *,
    now: datetime | None = None,
) -> None:
    moment = now or datetime.now(UTC)
    db = command.db
    try:
        tenant = await lock_tenant(db, command.tenant_id)
        require_current_version(tenant.updated_at, command.expected_version)
        if tenant.status == "deletion_scheduled":
            await db.rollback()
            return
        if tenant.status not in SCHEDULABLE:
            raise authorization_error("tenant_status_conflict")
        if confirm_slug != tenant.slug:
            raise authorization_error("tenant_deletion_slug_mismatch")
        previous = tenant.status
        due = moment + timedelta(days=settings.tenant_deletion_grace_days)
        tenant.status, tenant.deletion_due_at = "deletion_scheduled", due
        tenant.updated_at = bump_timestamp(tenant.updated_at)
        metadata = {"previous_status": previous, "deletion_due_at": due.isoformat()}
        await record(command, tenant, audit_actions.TENANT_DELETION_SCHEDULE, metadata, reason)
        await db.commit()
    except (AppError, SQLAlchemyError):
        await db.rollback()
        raise


async def cancel_tenant_deletion(command: TenantCommand, reason: str | None = None) -> None:
    db = command.db
    try:
        tenant = await lock_tenant(db, command.tenant_id)
        require_current_version(tenant.updated_at, command.expected_version)
        if tenant.status == "suspended" and tenant.deletion_due_at is None:
            await db.rollback()
            return
        if tenant.status != "deletion_scheduled":
            raise authorization_error("tenant_status_conflict")
        due = tenant.deletion_due_at
        tenant.status, tenant.deletion_due_at = "suspended", None
        tenant.updated_at = bump_timestamp(tenant.updated_at)
        metadata = {"status": "suspended", "cancelled_due_at": None if due is None else str(due)}
        await record(command, tenant, audit_actions.TENANT_DELETION_CANCEL, metadata, reason)
        await db.commit()
    except (AppError, SQLAlchemyError):
        await db.rollback()
        raise
