"""Settle what a leaving member owns and holds in one organization (plan 10.7, S15; E-02).

Runs inside ``preflight_member_exit`` after the member checks and before the resource
locks, under the organization row lock taken by ``members_write_context``:

1. Resolutions, credentials first so an agent/tool/MCP server whose own credential moves
   with it is no longer bound to the leaver when its transfer is checked:
   * ``transfer`` reuses E-01 ``transfer_row`` as governance (``can_manage_members``
     holders are the organization's governors): the new owner must be an active member,
     the actor may not name themselves unless they are the owner, the removal reason is
     the audit reason, no manager grant is kept for the leaver. Dependents owned by the
     leaver do not block — the exit stops them in the same transaction.
   * ``delete`` is offered for credentials only (personal accounts cannot be transferred,
     10.4), refused while a skill credential binding holds it (RESTRICT FK); agents
     are archived beforehand through the agent archive route and stay with their owner
     in the trash; skills, tools and MCP servers use their delete routes.
2. Every active grant naming the leaver in this organization is revoked through
   ``GrantService`` (8.6 markers + outbox), audit metadata ``member_exit``.

Per object the order is organization → grants → resource, as in E-01. Not done here:
the delegation validator (10.7 item 5, F-02/P6) and the ``delegation.broken`` /
``ownership.transferred`` notifications (P7).
"""

from dataclasses import dataclass, field
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.authz.error_codes import authorization_error
from app.authz.grant_validation import invalid
from app.authz.grants import GrantService
from app.authz.org_context import OrgContext
from app.authz.org_control import OrgControlContext
from app.authz.resource_lifecycle import on_resource_deleted
from app.authz.service import AuthzService
from app.authz.share_targets import SHARE_TYPES, ShareRow, load_target
from app.authz.share_transfer import transfer_row
from app.credentials import service as credential_service
from app.models.authz import ResourceGrant
from app.models.credential import Credential
from app.models.marketplace import SkillCredentialBinding
from app.schemas.grant import OwnershipTransfer
from app.schemas.org_member_exit import ExitResolution, MemberRemoval

RESOLUTION_ORDER: Final = ("credential", "tool", "mcp_server", "skill", "agent")


@dataclass(slots=True)
class ExitSettlementResult:
    transferred: list[UUID] = field(default_factory=list)
    deleted: list[UUID] = field(default_factory=list)
    revoked_grant_ids: list[UUID] = field(default_factory=list)
    outbox_ids: list[UUID] = field(default_factory=list)


async def credential_delete_blockers(db: AsyncSession, credential_id: UUID) -> dict[str, list[str]]:
    """Skill credential bindings hold the credential by ``ON DELETE RESTRICT``: checked
    before the delete (409 ``transfer_blocked``) instead of surfacing an IntegrityError."""
    bindings = await db.scalars(
        select(SkillCredentialBinding.id)
        .where(SkillCredentialBinding.credential_id == credential_id)
        .order_by(SkillCredentialBinding.id)
    )
    ids = [str(value) for value in bindings]
    return {"skill_bindings": ids} if ids else {}


def actor_context(context: OrgControlContext) -> OrgContext:
    return OrgContext(
        org_id=context.organization.id,
        tenant_id=context.organization.tenant_id,
        user_id=context.actor.id,
        is_super_user=context.actor.is_super_user,
    )


def held_grants_query(org_id: UUID, user_id: UUID) -> Select[tuple[ResourceGrant]]:
    return select(ResourceGrant).where(
        ResourceGrant.org_id == org_id,
        ResourceGrant.principal_type == "user",
        ResourceGrant.principal_id == user_id,
        ResourceGrant.state == "active",
    )


async def _owned(db: AsyncSession, ctx: OrgContext, item: ExitResolution, user: UUID) -> ShareRow:
    row = await load_target(db, ctx, SHARE_TYPES[item.resource_type], item.resource_id)
    if row is None or row.user_id != user:
        raise invalid("not_owned")
    return row


@dataclass(frozen=True, slots=True)
class MemberExitSettlement:
    db: AsyncSession
    authz: AuthzService
    context: OrgControlContext
    user_id: UUID
    removal: MemberRemoval | None = None  # None: plain DELETE, grants only
    service: GrantService = field(default_factory=GrantService)
    result: ExitSettlementResult = field(default_factory=ExitSettlementResult)

    async def __call__(self) -> None:
        ctx = actor_context(self.context)
        resolutions = self.removal.resolutions if self.removal else []
        ordered = sorted(resolutions, key=lambda item: RESOLUTION_ORDER.index(item.resource_type))
        for item in ordered:
            row = await _owned(self.db, ctx, item, self.user_id)
            if item.action == "delete":
                await self._delete(ctx, row)
            else:
                await self._transfer(ctx, item, row)
        await self.revoke_held()

    @property
    def _reason(self) -> str | None:
        return self.removal.reason if self.removal else None

    async def _transfer(self, ctx: OrgContext, item: ExitResolution, row: ShareRow) -> None:
        if item.new_owner_id is None:
            raise invalid("new_owner_required")
        body = OwnershipTransfer(new_owner_id=item.new_owner_id, reason=self._reason)
        kind = SHARE_TYPES[item.resource_type]
        done = await transfer_row(
            self.db, self.authz, ctx, kind, row, body, service=self.service, exiting=self.user_id
        )
        self.result.transferred.append(row.id)
        self.result.outbox_ids.extend(done.change.outbox_ids)

    async def _delete(self, ctx: OrgContext, row: ShareRow) -> None:
        if not isinstance(row, Credential):
            raise invalid("delete_unsupported")
        if blocking := await credential_delete_blockers(self.db, row.id):
            raise authorization_error("transfer_blocked", blocking)
        await credential_service.write_audit_log(
            self.db,
            credential_id=row.id,
            actor_user_id=ctx.user_id,
            action="delete",
            metadata={"member_exit": str(self.user_id), "reason": self._reason},
        )
        change = await on_resource_deleted(self.db, row, mode=self.authz.mode)
        await self.db.delete(row)
        await self.db.flush()
        self.result.deleted.append(row.id)
        self.result.outbox_ids.extend(change.outbox_ids)

    async def revoke_held(self) -> None:
        """Every active grant naming the leaver in the organization (idempotent)."""
        ctx = actor_context(self.context)
        query = held_grants_query(ctx.org_id, self.user_id).order_by(ResourceGrant.id)
        for grant in list(await self.db.scalars(query)):
            audit = {"member_exit": True, "governance": True}
            change = await self.service.revoke(self.db, ctx, grant.id, audit=audit)
            self.result.revoked_grant_ids.append(grant.id)
            self.result.outbox_ids.extend(change.outbox_ids)
