"""``POST /api/resources/{type}/{id}/transfer``: ownership transfer (9.7, 10.7, S17; E-01).

* Who: the owner or a ``can_govern`` holder (organization/company admin). A governor
  who is not the owner may not name themselves (governance must not become content
  access) and must give a reason.
* To whom: an active member of the resource's organization (D21). Naming the current
  owner returns the unchanged state (9.7.1 duplicate rule).
* Effects in one transaction: the owner column, the ``owner`` tuple swap with an
  ``ownership`` revocation marker (``on_owner_changed``), revocation of the new owner's
  now redundant direct grants, an optional ``manager`` grant for the previous owner and
  the ``ownership.transfer`` audit event. Delegations keep ``granted_by`` (10.7).
* Refused up front (``share_transfer_blocks``): personal account credentials (10.4) and
  resources with dependents bound to the previous owner (agent triggers, Agent API
  deployments/keys, the resource's own credential owned by the previous owner) —
  ``409 transfer_blocked`` lists them. Denied transfers write nothing, audit included.
* Not yet: the ``ownership.transferred`` notification (notifications land in P7); an
  agent's links to the previous owner's private skills/tools/MCP servers and its
  conversations stay as they are (see ``share_transfer_blocks``).
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.authz.grant_validation import GrantRequest, check_principal, invalid
from app.authz.grants import GrantService, GrantTarget
from app.authz.org_context import OrgContext
from app.authz.resource_lifecycle import LifecycleChange, on_owner_changed
from app.authz.service import AuthzService
from app.authz.share_targets import ShareRow, ShareType, authorize_target, is_owner
from app.authz.share_transfer_blocks import check_transferable
from app.authz.sharing import lock_org_row, share_grants
from app.models.agent import Agent
from app.models.skill import Skill
from app.schemas.grant import OwnershipTransfer, OwnershipTransferResult
from app.services import audit_service


@dataclass(frozen=True, slots=True)
class TransferResult:
    body: OwnershipTransferResult
    change: LifecycleChange


async def _check_name_free(db: AsyncSession, row: ShareRow, new_owner: UUID) -> None:
    """Skills are unique per (organization, owner, slug): S17 409."""
    if not isinstance(row, Skill):
        return
    clash = await db.scalar(
        select(Skill.id).where(
            Skill.org_id == row.org_id,
            Skill.user_id == new_owner,
            Skill.slug == row.slug,
            Skill.id != row.id,
        )
    )
    if clash is not None:
        raise authorization_error("transfer_name_conflict")


async def transfer(
    db: AsyncSession,
    authz: AuthzService,
    ctx: OrgContext,
    kind: ShareType,
    resource_id: UUID,
    body: OwnershipTransfer,
    *,
    service: GrantService | None = None,
) -> TransferResult:
    row = await authorize_target(db, authz, ctx, kind, resource_id, "can_govern")
    return await transfer_row(db, authz, ctx, kind, row, body, service=service)


async def transfer_row(
    db: AsyncSession,
    authz: AuthzService,
    ctx: OrgContext,
    kind: ShareType,
    row: ShareRow,
    body: OwnershipTransfer,
    *,
    service: GrantService | None = None,
    exiting: UUID | None = None,
) -> TransferResult:
    """The transfer once ``can_govern`` is established (the API above, or the member-exit
    flow of E-02 whose ``can_manage_members`` holders are the organization's governors).
    ``exiting``: dependents of that leaving member are stopped by the caller."""
    previous = row.user_id
    governing = not is_owner(row, ctx)
    reason = (body.reason or "").strip() or None
    if governing and body.new_owner_id == ctx.user_id:
        raise invalid("self_transfer")
    if governing and reason is None:
        raise invalid("reason_required")
    if isinstance(row, Agent) and row.archived_at is not None:
        raise invalid("archived")
    result = OwnershipTransferResult(
        resource_type=kind.name,
        resource_id=row.id,
        owner_id=body.new_owner_id,
        previous_owner_id=previous,
    )
    if body.new_owner_id == previous:
        return TransferResult(result, LifecycleChange())
    await lock_org_row(db, ctx.org_id)  # org → member row → grants → resource (E-02 order)
    await check_principal(db, ctx, GrantRequest("manager", "user", body.new_owner_id))
    await _check_name_free(db, row, body.new_owner_id)
    await check_transferable(db, row, previous, exiting=exiting)  # 409, nothing written
    grants = service or GrantService()
    outbox: list[UUID] = []
    for held in await share_grants(db, ctx, kind, row.id):
        if (held.principal_type, held.principal_id) == ("user", body.new_owner_id):
            outbox += (await grants.revoke(db, ctx, held.id)).outbox_ids
    model = kind.model
    try:  # compare-and-set after the org-row locks above (8.6 lock order); no row lock
        moved = await db.execute(
            update(model)
            .where(model.id == row.id, model.user_id == previous)
            .values(user_id=body.new_owner_id)
            .execution_options(synchronize_session="fetch")
        )
    except IntegrityError as error:  # a concurrent rename took the name
        raise authorization_error("transfer_name_conflict") from error
    if moved.rowcount != 1:  # type: ignore[attr-defined]  # CursorResult for DML
        raise authorization_error("stale_version")  # transferred concurrently
    owner_change = await on_owner_changed(db, row, previous_owner_id=previous, mode=authz.mode)
    outbox += owner_change.outbox_ids
    if body.keep_manager and previous is not None:
        request = GrantRequest("manager", "user", previous)
        kept = await grants.grant(db, ctx, GrantTarget(kind.name, row.id), request)
        outbox += kept.outbox_ids
        result.manager_grant_id = kept.grant.id
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=ctx.user_id,
        action=audit_actions.OWNERSHIP_TRANSFER,
        target_type=kind.name,
        target_id=row.id,
        target_owner_user_id=body.new_owner_id,
        outcome="success",
        metadata={
            "org_id": str(ctx.org_id),
            "previous_owner_id": str(previous) if previous else None,
            "new_owner_id": str(body.new_owner_id),
            "reason": reason,
            "keep_manager": body.keep_manager,
            "governance": governing,
            "manager_grant_id": str(result.manager_grant_id) if result.manager_grant_id else None,
        },
    )
    return TransferResult(result, LifecycleChange(tuple(outbox)))
