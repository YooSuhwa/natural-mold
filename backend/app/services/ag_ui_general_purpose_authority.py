"""Check the inherited root authority of Deep Agents' built-in chat subagent."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_policy import (
    RuntimePolicySnapshotError,
    validate_runtime_policy_snapshot,
)
from app.approvals.emergency import refuse_run
from app.authz.client import AuthzUnavailable
from app.authz.conversation_agent_checks import current_authz
from app.authz.denials import authz_unavailable
from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.scoping import private_scope_predicate
from app.child_tasks.errors import ChildTaskStopped
from app.guardrails.policy import resolve_tool_risk
from app.guardrails.tool_risk import level_of
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.organization import Organization
from app.tools.keys import DELEGATION_TOOL, ref_of


async def _original_chat_root(
    db: AsyncSession, context: OrgContext, conversation_id: UUID, runs: set[UUID]
) -> tuple[UUID, UUID, int, str]:
    # Ordinary stored CHAT agents supply linked specs only by agent_<UUID>, so
    # only the compiler can insert the reserved general-purpose role. Its graph
    # shares the root's tools, model account, permissions and approval policy.
    pin = ConversationRunAgent
    account = case((Agent.identity_mode == "per_user", context.user_id), else_=Agent.user_id)
    rows = (
        await db.execute(
            select(
                ConversationRun.id,
                Agent.id.label("root_id"),
                Agent.user_id.label("owner_id"),
                Organization.authz_epoch,
                Conversation.runtime_policy_snapshot,
                Conversation.runtime_policy_hash,
                Conversation.runtime_policy_version,
                Conversation.runtime_policy_source,
                ConversationRun.metadata_json,
                pin.path,
            )
            .select_from(ConversationRun)
            .join(Conversation, Conversation.id == ConversationRun.conversation_id)
            .join(Agent, Agent.id == Conversation.agent_id)
            .join(Organization, Organization.id == Conversation.org_id)
            .join(pin, pin.run_id == ConversationRun.id)
            .where(
                Conversation.id == conversation_id,
                private_scope_predicate(Conversation, context),
                Conversation.source.in_(("ui", "side_chat")),
                Conversation.agent_lifecycle_state == "active",
                Agent.archived_at.is_(None),
                Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
                Agent.org_id == context.org_id,
                Agent.tenant_id == context.tenant_id,
                Agent.identity_mode.in_(("fixed", "per_user")),
                ConversationRun.id.in_(runs),
                ConversationRun.status == "interrupted",
                ConversationRun.cancel_requested_at.is_(None),
                private_scope_predicate(ConversationRun, context),
                ConversationRun.agent_id == Agent.id,
                ConversationRun.run_source == "CHAT",
                ConversationRun.invoker_type == "user",
                ConversationRun.resource_owner_user_id == Agent.user_id,
                ConversationRun.credential_subject_user_id == account,
                ConversationRun.on_behalf_of_user_id == account,
                ConversationRun.runtime_policy_hash == Conversation.runtime_policy_hash,
                ConversationRun.runtime_policy_source == Conversation.runtime_policy_source,
                ConversationRun.runtime_policy_version == Conversation.runtime_policy_version,
                pin.role == "root",
                pin.agent_id == Agent.id,
                pin.agent_id_snapshot == Agent.id,
                pin.invoker_type == "user",
                pin.resource_owner_user_id == Agent.user_id,
                pin.on_behalf_of_user_id == account,
                pin.credential_subject_user_id == account,
                or_(
                    pin.identity_mode_at_accept.is_(None),
                    pin.identity_mode_at_accept == Agent.identity_mode,
                ),
            )
        )
    ).all()
    if not rows or {row.id for row in rows} != runs or any(row.path != [] for row in rows):
        raise ChildTaskStopped("revoked")
    for row in rows:
        metadata = row.metadata_json
        if metadata is not None and (
            not isinstance(metadata, dict) or "goal_followup_input_id" in metadata
        ):
            raise ChildTaskStopped("revoked")
    first = rows[0]
    try:
        policy = validate_runtime_policy_snapshot(
            first.runtime_policy_snapshot,
            first.runtime_policy_version,
            first.runtime_policy_hash,
            first.runtime_policy_source,
        )
    except RuntimePolicySnapshotError as exc:
        raise ChildTaskStopped("revoked") from exc
    if policy is None or policy.source not in {"stored", "legacy_compat"}:
        raise ChildTaskStopped("revoked")
    return first.root_id, first.owner_id, first.authz_epoch, policy.policy_hash


async def require_general_purpose_authority(
    db: AsyncSession, *, context: OrgContext, conversation_id: UUID, runs: set[UUID]
) -> UUID:
    """A native built-in invocation runs on the original, still-permitted root account."""
    current = await _original_chat_root(db, context, conversation_id, runs)
    root_id, owner, _, _ = current
    authz = current_authz()
    if authz.enforces("agent"):
        try:
            allowed = await authz.check(
                Principal.for_user(context),
                "can_run",
                f"agent:{root_id}",
                consistency="HIGHER_CONSISTENCY",
            )
        except AuthzUnavailable as exc:
            raise authz_unavailable() from exc
        if not allowed:
            raise ChildTaskStopped("revoked")
    elif owner != context.user_id:
        raise ChildTaskStopped("revoked")
    if current != await _original_chat_root(db, context, conversation_id, runs):
        raise ChildTaskStopped("revoked")
    await refuse_run(db, tenant_id=context.tenant_id, agent_id=root_id)
    risk = await resolve_tool_risk(db, context.tenant_id, AGENT_RUNTIME_PROFILE_STANDARD)
    if risk is not None and risk.blocks(ref_of(DELEGATION_TOOL), level_of(DELEGATION_TOOL)):
        raise ChildTaskStopped("revoked")
    return root_id
