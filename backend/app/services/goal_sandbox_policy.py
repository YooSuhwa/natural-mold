"""Reauthorize a goal workspace at each root chat skill boundary."""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import Table, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_sandbox import SkillSandboxError
from app.authz.agent_access import require_agent
from app.authz.execution_lock_cursor import ExecutionProofError
from app.authz.org_context import OrgContext
from app.authz.organization_execution_gateway import acquire_execution_proof
from app.authz.runtime import runtime_authz
from app.authz.scoping import private_scope_predicate
from app.authz.skill_access import authorize_skill
from app.config import settings
from app.database import Base
from app.error_codes import agent_not_found
from app.exceptions import AppError
from app.marketplace.skill_runtime import SkillRuntimeDescriptor, SkillToolContext
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.organization import Organization
from app.models.skill import AgentSkillLink, Skill
from app.services.goal_sandbox_leases import SandboxScope
from app.services.goal_sandbox_profile import (
    goal_sandbox_provider_fingerprint,
    measured_skill_hashes,
)


def _denied() -> SkillSandboxError:
    return SkillSandboxError("This goal workspace is no longer available to this run.")


def _uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except ValueError:
        return None


async def _authorize_agent(db: AsyncSession, actor: OrgContext, agent_id: UUID) -> None:
    authz = runtime_authz()
    owned = await db.scalar(
        select(Agent).where(Agent.id == agent_id, private_scope_predicate(Agent, actor))
    )
    agent = await require_agent(
        db, authz, actor, agent_id, "can_run", legacy=owned, not_found=agent_not_found()
    )
    if agent.archived_at is not None:
        raise _denied()


async def _approved_skill(
    db: AsyncSession, actor: OrgContext, agent_id: UUID, skill_id: UUID, skill_hash: str
) -> bool:
    skill = await authorize_skill(db, runtime_authz(), actor, skill_id, "can_use")
    link = await db.scalar(
        select(AgentSkillLink)
        .where(AgentSkillLink.agent_id == agent_id, AgentSkillLink.skill_id == skill.id)
        .execution_options(populate_existing=True)
        .with_for_update(of=AgentSkillLink, read=True)
    )
    if link is None or link.link_status != "active":
        raise _denied()
    # A separate delegator's grant is outside this direct-owner feature.
    return (
        link.delegated_by in (None, actor.user_id)
        and skill.kind == "package"
        and skill.content_hash == skill_hash
        and not skill.credential_requirements
        and not (link.config or {}).get("credential_bindings")
        and not (skill.execution_profile or {}).get("requires_network")
        and not (skill.execution_profile or {}).get("network_domains")
    )


async def _approved_skill_grants(
    db: AsyncSession,
    actor: OrgContext,
    agent_id: UUID,
    skill_id: UUID,
    skill_hash: str,
    hashes: tuple[str, ...],
) -> tuple[tuple[UUID, str], ...] | None:
    """Bind each reviewed package to exactly one currently linked, authorized skill."""
    await _authorize_agent(db, actor, agent_id)
    if skill_hash not in hashes or not await _approved_skill(
        db, actor, agent_id, skill_id, skill_hash
    ):
        return None
    approved = [(skill_id, skill_hash)]
    if len(hashes) == 1:
        return tuple(approved)
    rows = (
        await db.execute(
            select(Skill.id, Skill.content_hash)
            .join(AgentSkillLink, AgentSkillLink.skill_id == Skill.id)
            .where(AgentSkillLink.agent_id == agent_id, Skill.content_hash.in_(hashes))
            .order_by(Skill.id)
        )
    ).all()
    if len(rows) != len(hashes) or {row[1] for row in rows} != set(hashes):
        raise _denied()
    for other_id, digest in rows:
        if other_id == skill_id:
            continue
        if not await _approved_skill(db, actor, agent_id, other_id, digest):
            return None
        approved.append((other_id, digest))
    return tuple(sorted(approved))


async def _conversation(
    db: AsyncSession, actor: OrgContext, agent_id: UUID, conversation_id: UUID
) -> Conversation:
    proof = await acquire_execution_proof(
        db, actor, root_agent_id=agent_id, conversation_ids=(conversation_id,)
    )
    if not proof.covers_conversation(conversation_id):
        raise _denied()
    conversation = await db.get(Conversation, conversation_id, populate_existing=True)
    if (
        conversation is None
        or conversation.agent_id != agent_id
        or conversation.source != "ui"
        or conversation.side_chat_parent_id is not None
        or conversation.agent_lifecycle_state != "active"
    ):
        raise _denied()
    return conversation


def _goal_tables() -> tuple[Table | None, Table | None]:
    return Base.metadata.tables.get("conversation_goals"), Base.metadata.tables.get("goal_attempts")


async def _active_goal(
    db: AsyncSession,
    actor: OrgContext,
    conversation_id: UUID,
    *,
    attempt_id: UUID | None = None,
    run_id: UUID | None = None,
    scope: SandboxScope | None = None,
) -> tuple[UUID, int] | None:
    # Feature availability follows the registered goal schema. An installation
    # without goal attempts has no workspaces eligible for retention.
    goals, attempts = _goal_tables()
    if goals is None or attempts is None:
        return None
    query = select(goals.c.id, goals.c.version).where(
        goals.c.conversation_id == conversation_id,
        goals.c.tenant_id == actor.tenant_id,
        goals.c.org_id == actor.org_id,
        goals.c.user_id == actor.user_id,
        goals.c.status == "active",
    )
    if scope is not None:
        query = query.where(goals.c.id == scope.goal_id, goals.c.version == scope.goal_version)
    else:
        query = query.join(attempts, goals.c.id == attempts.c.goal_id).where(
            attempts.c.id == attempt_id,
            attempts.c.run_id == run_id,
            attempts.c.status == "pending",
            attempts.c.goal_version == goals.c.version,
        )
    row = (await db.execute(query.with_for_update(read=True))).first()
    return (row[0], row[1]) if row else None


async def goal_scope_is_current(db: AsyncSession, scope: SandboxScope) -> bool:
    """Authorize recovery publication on a fresh tenant transaction, including after disable."""
    actor = OrgContext(scope.org_id, scope.tenant_id, scope.user_id)
    try:
        conversation = await _conversation(db, actor, scope.agent_id, scope.conversation_id)
        return (
            conversation.runtime_policy_hash == scope.policy_hash
            and conversation.runtime_policy_version == scope.policy_version
            and await _active_goal(db, actor, conversation.id, scope=scope) is not None
            and await _approved_skill_grants(
                db,
                actor,
                scope.agent_id,
                scope.skill_id,
                scope.skill_hash,
                tuple(digest for _, digest in scope.approved_skills),
            )
            == scope.approved_skills
        )
    except AppError as exc:
        if exc.status >= 500:
            # A temporarily unavailable authorization backend cannot decide
            # whether a pending output must be discarded.
            raise
        return False
    except SkillSandboxError:
        return False


async def resolve_goal_sandbox_scope(
    db: AsyncSession,
    ctx: SkillToolContext,
    descriptor: SkillRuntimeDescriptor,
    *,
    skill_hash: str,
    in_flight: SandboxScope | None = None,
) -> SandboxScope | None:
    """Return the server-bound scope. In-flight rechecks only authorize output recovery."""
    if in_flight is None:
        if (
            not settings.goal_sandbox_enabled
            or not ctx.goal_sandbox_run_id
            or descriptor.credential_bindings
            or (descriptor.execution_profile or {}).get("requires_network")
            or (descriptor.execution_profile or {}).get("network_domains")
        ):
            return None
        hashes = await asyncio.to_thread(measured_skill_hashes, skill_hash)
        if hashes is None:
            return None
    elif (
        descriptor.credential_bindings
        or (descriptor.execution_profile or {}).get("requires_network")
        or (descriptor.execution_profile or {}).get("network_domains")
    ):
        raise _denied()
    else:
        hashes = tuple(digest for _, digest in in_flight.approved_skills)
    run_id, conversation_id = _uuid(ctx.goal_sandbox_run_id), _uuid(ctx.thread_id)
    if run_id is None or conversation_id is None:
        raise _denied()
    run = await db.get(ConversationRun, run_id, populate_existing=True)
    if (
        run is None
        or run.org_id is None
        or run.tenant_id != ctx.tenant_id
        or run.conversation_id != conversation_id
        or run.agent_id is None
        or run.agent_id != ctx.agent_id
        or run.run_source != "CHAT"
        or run.invoker_type != "user"
    ):
        raise _denied()
    if ctx.user_id != run.user_id:
        # Fixed-identity execution for a different actor uses its per-call workspace.
        if in_flight is None and ctx.user_id == run.credential_subject_user_id:
            return None
        raise _denied()
    actor = OrgContext(run.org_id, ctx.tenant_id, run.user_id)
    attempt_id = _uuid((run.metadata_json or {}).get("goal_attempt_id"))
    if in_flight is None and attempt_id is None:
        return None
    if in_flight is None:
        org = await db.get(Organization, actor.org_id, populate_existing=True)
        if org is None or (org.settings or {}).get("goal_sandbox_enabled") is not True:
            return None
    try:
        conversation = await _conversation(db, actor, run.agent_id, conversation_id)
        # The gateway locks the conversation before the run. Refresh under a
        # shared lock so cancel/goal edit cannot race acceptance of this lease.
        await db.refresh(run, with_for_update={"read": True, "of": ConversationRun})
        if (
            run.cancel_requested_at is not None
            or run.status != "running"
            or not run.is_active
            or run.credential_subject_user_id != run.user_id
            or run.on_behalf_of_user_id != run.user_id
            or run.runtime_policy_hash is None
            or run.runtime_policy_version is None
            or run.runtime_policy_hash != conversation.runtime_policy_hash
            or run.runtime_policy_version != conversation.runtime_policy_version
            or attempt_id != _uuid((run.metadata_json or {}).get("goal_attempt_id"))
        ):
            raise _denied()
        org = await db.get(Organization, actor.org_id, populate_existing=True)
        if in_flight is None and (
            org is None or (org.settings or {}).get("goal_sandbox_enabled") is not True
        ):
            return None
        goal = await _active_goal(db, actor, conversation_id, attempt_id=attempt_id, run_id=run_id)
        if goal is None:
            raise _denied()
        approved = await _approved_skill_grants(
            db, actor, run.agent_id, descriptor.id, skill_hash, hashes
        )
        if approved is None:
            if in_flight is not None:
                raise _denied()
            return None
        scope = SandboxScope(
            tenant_id=actor.tenant_id,
            org_id=actor.org_id,
            user_id=actor.user_id,
            conversation_id=conversation_id,
            goal_id=goal[0],
            goal_version=goal[1],
            agent_id=run.agent_id,
            policy_hash=run.runtime_policy_hash,
            policy_version=run.runtime_policy_version,
            skill_id=approved[0][0],
            skill_hash=approved[0][1],
            skill_set=approved if len(approved) > 1 else (),
            image=goal_sandbox_provider_fingerprint(),
        )
        if in_flight is not None and scope != in_flight:
            raise _denied()
        return scope
    except (AppError, ExecutionProofError) as exc:
        raise _denied() from exc
