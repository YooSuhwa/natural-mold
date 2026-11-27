"""Authorize a follow-up on one child admitted to this exact conversation run."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, lazyload

from app.agent_runtime.prepared_run_registry import PreparedRunLeaseError, get_prepared_run_registry
from app.agent_runtime.runtime_hydration_guard import active_lease_run
from app.approvals.emergency import refuse_run
from app.authz.client import AuthzUnavailable
from app.authz.conversation_agent_checks import current_authz
from app.authz.denials import authz_unavailable
from app.authz.execution_lock_cursor import LockClass
from app.authz.org_context import OrgContext
from app.authz.organization_execution_gateway import acquire_execution_proof, lock_row
from app.authz.organization_execution_proof import live_proof
from app.authz.principal import Principal
from app.authz.scoping import private_scope_predicate
from app.exceptions import AppError
from app.guardrails.policy import resolve_tool_risk
from app.models.agent import Agent
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.organization import Organization
from app.services.child_task_errors import ChildTaskStopped
from app.tools.keys import agent_ref


@dataclass(frozen=True, slots=True)
class _Target:
    conversation_id: UUID
    parent_id: UUID
    parent_owner: UUID
    parent_identity: str
    profile: str
    child_owner: UUID
    child_name: str
    child_profile: str
    epoch: int


async def _target(db: AsyncSession, context: OrgContext, run_id: UUID, child_id: UUID) -> _Target:
    """Read authority metadata only. Never load a child graph, model or credential here."""
    child = aliased(Agent)
    root_pin = aliased(ConversationRunAgent)
    child_pin = ConversationRunAgent
    root_account = case((Agent.identity_mode == "per_user", context.user_id), else_=Agent.user_id)
    child_account = case((child.identity_mode == "per_user", context.user_id), else_=child.user_id)
    row = (
        await db.execute(
            select(
                Conversation.id,
                Agent.id,
                Agent.user_id,
                Agent.identity_mode,
                Agent.runtime_profile,
                child.user_id,
                child.name,
                child.runtime_profile,
                Organization.authz_epoch,
            )
            .select_from(ConversationRun)
            .join(Conversation, Conversation.id == ConversationRun.conversation_id)
            .join(Agent, Agent.id == ConversationRun.agent_id)
            .join(Organization, Organization.id == Conversation.org_id)
            .join(AgentSubAgentLink, AgentSubAgentLink.parent_agent_id == Agent.id)
            .join(child, child.id == AgentSubAgentLink.sub_agent_id)
            .join(root_pin, root_pin.run_id == ConversationRun.id)
            .join(child_pin, child_pin.run_id == ConversationRun.id)
            .where(
                ConversationRun.id == run_id,
                ConversationRun.user_id == context.user_id,
                ConversationRun.org_id == context.org_id,
                ConversationRun.tenant_id == context.tenant_id,
                private_scope_predicate(Conversation, context),
                Conversation.agent_id == Agent.id,
                Agent.org_id == context.org_id,
                Agent.tenant_id == context.tenant_id,
                Agent.archived_at.is_(None),
                child.org_id == context.org_id,
                child.tenant_id == context.tenant_id,
                child.id == child_id,
                child.archived_at.is_(None),
                AgentSubAgentLink.link_status == "active",
                ConversationRun.run_source == "CHAT",
                ConversationRun.resource_owner_user_id == Agent.user_id,
                ConversationRun.credential_subject_user_id == root_account,
                ConversationRun.on_behalf_of_user_id == root_account,
                root_pin.role == "root",
                root_pin.agent_id == Agent.id,
                root_pin.agent_id_snapshot == Agent.id,
                root_pin.identity_mode_at_accept == Agent.identity_mode,
                root_pin.resource_owner_user_id == Agent.user_id,
                root_pin.credential_subject_user_id == root_account,
                root_pin.on_behalf_of_user_id == root_account,
                child_pin.role == "subagent",
                child_pin.agent_id == child_id,
                child_pin.agent_id_snapshot == child_id,
                child_pin.identity_mode_at_accept == child.identity_mode,
                child_pin.resource_owner_user_id == child.user_id,
                child_pin.credential_subject_user_id == child_account,
                child_pin.on_behalf_of_user_id == child_account,
            )
        )
    ).one_or_none()
    if row is None:
        raise ChildTaskStopped("revoked")
    return _Target(*row)


async def _require_delegation(target: _Target, context: OrgContext, child_id: UUID) -> None:
    """The chat caller runs the parent; its fixed or caller identity runs the child."""
    authz = current_authz()
    account = context.user_id if target.parent_identity == "per_user" else target.parent_owner
    if target.parent_identity not in {"per_user", "fixed"}:
        raise ChildTaskStopped("revoked")
    if not authz.enforces("agent"):
        if target.parent_owner != context.user_id or target.child_owner != context.user_id:
            raise ChildTaskStopped("revoked")
        return
    if (target.parent_owner != context.user_id and target.profile != "standard") or (
        target.child_owner != context.user_id and target.child_profile != "standard"
    ):
        raise ChildTaskStopped("revoked")

    caller = Principal.for_user(context)
    try:
        if not await authz.check(
            caller, "can_run", f"agent:{target.parent_id}", consistency="HIGHER_CONSISTENCY"
        ):
            raise ChildTaskStopped("revoked")
        if account != target.child_owner:
            subject = (
                caller
                if target.parent_identity == "per_user"
                else Principal(
                    "agent", target.parent_id, context.org_id, acting_user_id=context.user_id
                )
            )
            if not await authz.check(
                subject, "can_run", f"agent:{child_id}", consistency="HIGHER_CONSISTENCY"
            ):
                raise ChildTaskStopped("revoked")
    except AuthzUnavailable as exc:
        raise authz_unavailable() from exc


def _require_status(run: ConversationRun, require_active: bool) -> None:
    if require_active and (
        run.cancel_requested_at is not None or run.status in {"canceling", "canceled"}
    ):
        raise ChildTaskStopped("cancelled")
    if require_active and (not run.is_active or run.status != "running"):
        raise ChildTaskStopped("parent_terminal")


async def authorize_child_target(
    db: AsyncSession,
    context: OrgContext,
    run_id: UUID,
    child_agent_id: UUID,
    *,
    require_active: bool = True,
) -> ConversationRun:
    """Return the locked parent. Caller ends this short transaction before waiting or dispatch.

    Remote decisions precede the locks. Under the canonical prefix we check that neither
    the organization grant epoch nor the admitted link/identity changed during the decision.
    Prepared workers additionally enter their catalog-bound child control permit; HTTP readers
    in other workers only perform the durable target checks.
    """
    cached = db.info.get("moldy_child_task_authorization")
    proof = live_proof(db)
    if proof is not None:
        if cached is None or cached[:4] != (proof, context, run_id, child_agent_id):
            raise ChildTaskStopped("revoked")
        run = cached[4]
        _require_status(run, require_active)
        return run

    target = await _target(db, context, run_id, child_agent_id)
    await _require_delegation(target, context, child_agent_id)
    try:
        proof = await acquire_execution_proof(
            db, context, root_agent_id=target.parent_id, conversation_ids=(target.conversation_id,)
        )
        if (
            not proof.covers_conversation(target.conversation_id)
            or child_agent_id not in proof.agent_ids
            or target != await _target(db, context, run_id, child_agent_id)
        ):
            raise ChildTaskStopped("revoked")
        policy = await resolve_tool_risk(db, context.tenant_id, target.profile)
        if policy and policy.switched_off(agent_ref(child_agent_id, target.child_name)):
            raise ChildTaskStopped("revoked")
        await refuse_run(db, tenant_id=context.tenant_id, agent_id=target.parent_id)
        await refuse_run(db, tenant_id=context.tenant_id, agent_id=child_agent_id)
    except AppError as exc:
        if exc.status in {403, 404, 409}:
            raise ChildTaskStopped("revoked") from None
        raise
    run = await lock_row(
        db,
        proof.cursor,
        LockClass.RUN,
        run_id,
        select(ConversationRun)
        .options(lazyload("*"))
        .where(ConversationRun.id == run_id)
        .with_for_update(of=ConversationRun),
    )
    if run is None:
        raise ChildTaskStopped("revoked")
    lease_id = active_lease_run()
    if lease_id is not None:
        if lease_id != str(run_id) or run.runtime_materialization_sha256 is None:
            raise ChildTaskStopped("revoked")
        try:
            get_prepared_run_registry().ensure_live_for_marker(
                run_id, marker_digest=run.runtime_materialization_sha256
            )
        except PreparedRunLeaseError:
            raise ChildTaskStopped("revoked") from None
    _require_status(run, require_active)
    db.info["moldy_child_task_authorization"] = (proof, context, run_id, child_agent_id, run)
    return run


async def require_same_child_identity(
    db: AsyncSession, *, previous_run_id: UUID, current_run_id: UUID, child_agent_id: UUID
) -> None:
    """A resumed task retains the identities pinned before it first asked for approval."""
    proof = live_proof(db)
    if proof is None or not proof.cursor.holds(LockClass.RUN, current_run_id):
        raise ChildTaskStopped("revoked")
    scope = proof.scope
    context = OrgContext(scope.org_id, scope.tenant_id, scope.user_id)
    original = await _target(db, context, previous_run_id, child_agent_id)
    current = await _target(db, context, current_run_id, child_agent_id)
    # Each snapshot was matched to the same locked Agent, link and current identity.
    if original != current or not proof.covers_conversation(current.conversation_id):
        raise ChildTaskStopped("revoked")
