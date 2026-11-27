"""Authorization and goal-attempt binding at the retained skill-workspace boundary."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, Uuid, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_sandbox import SkillSandboxError
from app.marketplace.skill_runtime import (
    ResolvedCredential,
    SkillRuntimeDescriptor,
    SkillToolContext,
)
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.organization import Organization, OrganizationMember
from app.models.skill import AgentSkillLink, Skill
from app.services import goal_sandbox_policy as policy
from app.services.goal_sandbox_leases import SandboxScope
from tests.authz.execution_gateway_testkit import GatewayGraph, seed_graph

SKILL_HASH = "a" * 64
POLICY_HASH = "b" * 64


@dataclass
class BoundRun:
    graph: GatewayGraph
    goal_id: UUID
    attempt_id: UUID
    run_id: UUID
    ctx: SkillToolContext
    descriptor: SkillRuntimeDescriptor

    async def resolve(
        self, db: AsyncSession, *, in_flight: SandboxScope | None = None
    ) -> SandboxScope | None:
        return await policy.resolve_goal_sandbox_scope(
            db, self.ctx, self.descriptor, skill_hash=SKILL_HASH, in_flight=in_flight
        )


@pytest.fixture
async def goal_tables(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> tuple[Table, Table]:
    goals, attempts = policy._goal_tables()
    if goals is not None and attempts is not None:
        return goals, attempts
    # An installation may run F12 against a separately registered F01 schema.
    # Exercise the same UUID/version joins in SQLite without registering fake ORM models.
    metadata = MetaData()
    goals = Table(
        "policy_test_goals",
        metadata,
        Column("id", Uuid, primary_key=True),
        *(
            Column(key, Uuid, nullable=False)
            for key in ("conversation_id", "org_id", "tenant_id", "user_id")
        ),
        Column("status", String),
        Column("version", Integer),
        Column("title", String),
        Column("criteria", JSON),
    )
    attempts = Table(
        "policy_test_attempts",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("goal_id", Uuid),
        Column("run_id", Uuid),
        Column("goal_version", Integer),
        Column("status", String),
    )
    conn = await db.connection()
    await conn.run_sync(metadata.create_all)
    # Only these temporary test tables can survive the ordinary ORM teardown.
    await db.execute(attempts.delete())
    await db.execute(goals.delete())
    await db.commit()
    monkeypatch.setattr(policy, "_goal_tables", lambda: (goals, attempts))
    return goals, attempts


@pytest.fixture
async def bound(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    goal_tables: tuple[Table, Table],
) -> BoundRun:
    monkeypatch.setattr(policy.settings, "goal_sandbox_enabled", True)
    monkeypatch.setattr(
        policy,
        "measured_skill_hashes",
        lambda value: (SKILL_HASH,) if value == SKILL_HASH else None,
    )
    goals, attempts = goal_tables
    graph = await seed_graph(db)
    actor = graph.context
    org = await db.get(Organization, actor.org_id)
    conversation = await db.get(Conversation, graph.conversation_ids[0])
    assert org and conversation
    org.settings = {"goal_sandbox_enabled": True}
    conversation.runtime_policy_version = 1
    conversation.runtime_policy_hash = POLICY_HASH
    skill = Skill(
        name="Process files",
        slug="process-files",
        kind="package",
        user_id=actor.user_id,
        org_id=actor.org_id,
        tenant_id=actor.tenant_id,
        content_hash=SKILL_HASH,
    )
    goal_id = uuid4()
    await db.execute(
        goals.insert().values(
            id=goal_id,
            version=1,
            status="active",
            title="Process files",
            criteria=[{"type": "artifact"}],
            conversation_id=conversation.id,
            tenant_id=actor.tenant_id,
            org_id=actor.org_id,
            user_id=actor.user_id,
        )
    )
    attempt_id = uuid4()
    run = ConversationRun(
        conversation_id=conversation.id,
        agent_id=graph.agent_id,
        user_id=actor.user_id,
        tenant_id=actor.tenant_id,
        org_id=actor.org_id,
        source="chat",
        run_source="CHAT",
        invoker_type="user",
        status="running",
        is_active=True,
        on_behalf_of_user_id=actor.user_id,
        credential_subject_user_id=actor.user_id,
        runtime_policy_hash=POLICY_HASH,
        runtime_policy_version=1,
        metadata_json={"goal_attempt_id": str(attempt_id)},
    )
    db.add_all([skill, run])
    await db.flush()
    db.add(AgentSkillLink(agent_id=graph.agent_id, skill_id=skill.id))
    await db.execute(
        attempts.insert().values(
            id=attempt_id,
            goal_id=goal_id,
            run_id=run.id,
            goal_version=1,
            status="pending",
        )
    )
    await db.commit()
    return BoundRun(
        graph,
        goal_id,
        attempt_id,
        run.id,
        SkillToolContext(
            thread_id=str(conversation.id),
            user_id=actor.user_id,
            agent_id=graph.agent_id,
            tenant_id=actor.tenant_id,
            output_dir=tmp_path,
            runtime_root=tmp_path,
            goal_sandbox_run_id=str(run.id),
        ),
        SkillRuntimeDescriptor(
            id=skill.id,
            slug=skill.slug,
            name=skill.name,
            description="",
            original_storage_path=tmp_path,
            runtime_storage_path=tmp_path,
        ),
    )


async def test_run_is_bound_to_its_accepted_goal_and_owner(
    db: AsyncSession, bound: BoundRun
) -> None:
    scope = await bound.resolve(db)
    assert scope is not None
    assert (scope.goal_id, scope.goal_version, scope.skill_id, scope.skill_hash) == (
        bound.goal_id,
        1,
        bound.descriptor.id,
        SKILL_HASH,
    )
    assert (
        scope.tenant_id,
        scope.org_id,
        scope.user_id,
        scope.agent_id,
        scope.conversation_id,
    ) == (
        bound.ctx.tenant_id,
        bound.graph.context.org_id,
        bound.ctx.user_id,
        bound.ctx.agent_id,
        bound.graph.conversation_ids[0],
    )
    assert scope.policy_hash == POLICY_HASH
    await db.commit()
    assert await bound.resolve(db, in_flight=scope) == scope
    await db.commit()
    assert await policy.goal_scope_is_current(db, scope)
    await db.commit()
    assert not await policy.goal_scope_is_current(db, replace(scope, org_id=uuid4()))


@pytest.mark.parametrize(
    "change", ["goal_edit", "goal_pause", "attempt_completed", "other_attempt"]
)
async def test_old_run_never_adopts_a_replacement_attempt(
    db: AsyncSession, bound: BoundRun, change: str, goal_tables: tuple[Table, Table]
) -> None:
    goals, attempts = goal_tables
    scope = await bound.resolve(db)
    assert scope is not None
    await db.commit()
    if change == "goal_edit":
        await db.execute(update(goals).where(goals.c.id == bound.goal_id).values(version=2))
    elif change == "goal_pause":
        await db.execute(update(goals).where(goals.c.id == bound.goal_id).values(status="paused"))
    elif change == "attempt_completed":
        await db.execute(
            update(attempts).where(attempts.c.id == bound.attempt_id).values(status="completed")
        )
    else:
        other_run = ConversationRun(
            conversation_id=bound.graph.conversation_ids[0],
            agent_id=bound.graph.agent_id,
            user_id=bound.graph.context.user_id,
            org_id=bound.graph.context.org_id,
            tenant_id=bound.graph.context.tenant_id,
            source="chat",
            status="completed",
        )
        db.add(other_run)
        await db.flush()
        await db.execute(
            update(attempts).where(attempts.c.id == bound.attempt_id).values(run_id=other_run.id)
        )
    await db.commit()
    with pytest.raises(SkillSandboxError):
        await bound.resolve(db, in_flight=scope)
    await db.commit()
    with pytest.raises(SkillSandboxError):
        await bound.resolve(db)


@pytest.mark.parametrize(
    "change", ["membership", "broken_link", "cancel", "new_policy", "side_chat"]
)
async def test_fresh_authority_needed_to_execute_or_publish(
    db: AsyncSession, bound: BoundRun, change: str
) -> None:
    scope = await bound.resolve(db)
    assert scope is not None
    await db.commit()
    if change == "membership":
        await db.execute(
            delete(OrganizationMember).where(
                OrganizationMember.org_id == scope.org_id,
                OrganizationMember.user_id == scope.user_id,
            )
        )
    elif change == "broken_link":
        await db.execute(delete(AgentSkillLink).where(AgentSkillLink.skill_id == scope.skill_id))
    elif change == "cancel":
        await db.execute(
            update(ConversationRun)
            .where(ConversationRun.id == bound.run_id)
            .values(cancel_requested_at=datetime.now(UTC).replace(tzinfo=None))
        )
    elif change == "new_policy":
        await db.execute(
            update(Conversation)
            .where(Conversation.id == scope.conversation_id)
            .values(runtime_policy_hash="c" * 64)
        )
    else:
        await db.execute(
            update(Conversation)
            .where(Conversation.id == scope.conversation_id)
            .values(side_chat_parent_id=bound.graph.conversation_ids[1])
        )
    await db.commit()
    with pytest.raises(SkillSandboxError):
        await bound.resolve(db, in_flight=scope)
    await db.commit()
    with pytest.raises(SkillSandboxError):
        await bound.resolve(db)
    if change != "cancel":
        await db.commit()
        assert not await policy.goal_scope_is_current(db, scope)


@pytest.mark.parametrize("disable", ["organization", "deployment"])
async def test_disable_redirects_new_work_and_allows_in_flight_recovery(
    db: AsyncSession, bound: BoundRun, monkeypatch: pytest.MonkeyPatch, disable: str
) -> None:
    scope = await bound.resolve(db)
    assert scope is not None
    await db.commit()
    if disable == "organization":
        await db.execute(
            update(Organization).where(Organization.id == scope.org_id).values(settings={})
        )
    else:
        monkeypatch.setattr(policy.settings, "goal_sandbox_enabled", False)
    await db.commit()
    assert await bound.resolve(db) is None
    await db.commit()
    assert await bound.resolve(db, in_flight=scope) == scope
    await db.commit()
    assert await policy.goal_scope_is_current(db, scope)


@pytest.mark.parametrize("change", ["credentials", "network", "link_credentials", "changed_bytes"])
async def test_retained_environment_is_denied_for_ineligible_packages(
    db: AsyncSession, bound: BoundRun, change: str
) -> None:
    scope = await bound.resolve(db)
    assert scope is not None
    await db.commit()
    if change == "credentials":
        bound.descriptor.credential_bindings = {"test": ResolvedCredential(uuid4(), "test", {}, {})}
    elif change == "network":
        await db.execute(
            update(Skill)
            .where(Skill.id == scope.skill_id)
            .values(execution_profile={"network_domains": ["example.org"]})
        )
    elif change == "link_credentials":
        await db.execute(
            update(AgentSkillLink)
            .where(AgentSkillLink.skill_id == scope.skill_id)
            .values(config={"credential_bindings": {"test": str(uuid4())}})
        )
    else:
        await db.execute(
            update(Skill).where(Skill.id == scope.skill_id).values(content_hash="d" * 64)
        )
    await db.commit()
    assert await bound.resolve(db) is None
    await db.commit()
    with pytest.raises(SkillSandboxError):
        await bound.resolve(db, in_flight=scope)


@pytest.mark.parametrize("field", ["tenant_id", "user_id", "agent_id", "thread_id"])
async def test_worker_cannot_retarget_an_existing_run(
    db: AsyncSession, bound: BoundRun, field: str
) -> None:
    invalid_actor = replace(bound.ctx, **{field: str(uuid4()) if field == "thread_id" else uuid4()})
    with pytest.raises(SkillSandboxError):
        await policy.resolve_goal_sandbox_scope(
            db, invalid_actor, bound.descriptor, skill_hash=SKILL_HASH
        )


async def test_both_approved_skills_use_the_same_workspace_and_keep_their_grants(
    db: AsyncSession, bound: BoundRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewed_hash = "e" * 64
    approved_pair = tuple(sorted((SKILL_HASH, reviewed_hash)))
    monkeypatch.setattr(
        policy,
        "measured_skill_hashes",
        lambda digest: approved_pair if digest in approved_pair else None,
    )
    actor = bound.graph.context
    reviewer = Skill(
        name="Review files",
        slug="review-files",
        kind="package",
        user_id=actor.user_id,
        org_id=actor.org_id,
        tenant_id=actor.tenant_id,
        content_hash=reviewed_hash,
    )
    db.add(reviewer)
    await db.flush()
    db.add(AgentSkillLink(agent_id=bound.graph.agent_id, skill_id=reviewer.id))
    await db.commit()
    scope = await bound.resolve(db)
    assert scope is not None
    await db.commit()
    reviewer_descriptor = replace(bound.descriptor, id=reviewer.id, slug=reviewer.slug)
    reviewed_scope = await policy.resolve_goal_sandbox_scope(
        db, bound.ctx, reviewer_descriptor, skill_hash=reviewed_hash
    )
    assert reviewed_scope == scope
    await db.commit()
    await db.execute(delete(AgentSkillLink).where(AgentSkillLink.skill_id == reviewer.id))
    await db.commit()
    assert not await policy.goal_scope_is_current(db, scope)
    await db.commit()
    with pytest.raises(SkillSandboxError):
        await bound.resolve(db, in_flight=scope)


async def test_ambiguous_approved_hash_cannot_choose_another_link(
    db: AsyncSession, bound: BoundRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = bound.graph.context
    other_hash = "e" * 64
    monkeypatch.setattr(policy, "measured_skill_hashes", lambda digest: (SKILL_HASH, other_hash))
    for slug in ("reviewer-one", "reviewer-two"):
        reviewer = Skill(
            name=slug,
            slug=slug,
            kind="package",
            user_id=actor.user_id,
            org_id=actor.org_id,
            tenant_id=actor.tenant_id,
            content_hash=other_hash,
        )
        db.add(reviewer)
        await db.flush()
        db.add(AgentSkillLink(agent_id=bound.graph.agent_id, skill_id=reviewer.id))
    await db.commit()
    with pytest.raises(SkillSandboxError):
        await bound.resolve(db)
