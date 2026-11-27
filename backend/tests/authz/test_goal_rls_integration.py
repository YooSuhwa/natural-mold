"""Goal receipts keep the conversation, its owner and its run together under PostgreSQL RLS."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from app.models import (
    Agent,
    Conversation,
    ConversationGoal,
    ConversationRun,
    ConversationRunInput,
    GoalAttempt,
    GoalTransitionIntent,
    Model,
    Organization,
    User,
)
from tests.authz.i06_rls_lab import RlsLab, rls_lab

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class ConversationRows:
    tenant: UUID
    org: UUID
    owner: UUID
    conversation: UUID
    run: UUID
    queued_input: UUID


@pytest.fixture(scope="module")
def lab() -> Iterator[RlsLab]:
    with rls_lab() as value:
        yield value


def _actor(db: Connection, tenant: UUID) -> tuple[UUID, UUID, UUID, UUID]:
    org, owner, model, agent = (uuid4() for _ in range(4))
    db.execute(sa.insert(Organization).values(id=org, tenant_id=tenant, name="Org", slug=org.hex))
    db.execute(sa.insert(User).values(id=owner, name="Owner", email=f"{owner.hex}@users.test"))
    db.execute(
        sa.insert(Model).values(id=model, provider="test", model_name="test", display_name="test")
    )
    scope = {"org_id": org, "tenant_id": tenant, "user_id": owner}
    db.execute(
        sa.insert(Agent).values(
            id=agent,
            **scope,
            name="Agent",
            system_prompt="test",
            model_id=model,
            runtime_name=agent.hex,
        )
    )
    return tenant, org, owner, agent


def _conversation(db: Connection, actor: tuple[UUID, UUID, UUID, UUID]) -> ConversationRows:
    tenant, org, owner, agent = actor
    conv, run, queued = uuid4(), uuid4(), uuid4()
    scope = {"org_id": org, "tenant_id": tenant, "user_id": owner}
    db.execute(
        sa.insert(Conversation).values(
            id=conv,
            **scope,
            agent_id=agent,
            agent_name_snapshot="Agent",
            last_activity_at=datetime.now(UTC),
        )
    )
    db.execute(
        sa.insert(ConversationRun).values(
            id=run, **scope, agent_id=agent, conversation_id=conv, source="chat", status="completed"
        )
    )
    db.execute(
        sa.insert(ConversationRunInput).values(
            id=queued,
            conversation_id=conv,
            agent_id=agent,
            user_id=owner,
            position=1,
            client_request_id=queued.hex,
            source="goal_followup",
            input_payload={},
        )
    )
    return ConversationRows(tenant, org, owner, conv, run, queued)


def _goal(rows: ConversationRows, goal_id: UUID) -> sa.Insert:
    return sa.insert(ConversationGoal).values(
        id=goal_id,
        tenant_id=rows.tenant,
        org_id=rows.org,
        user_id=rows.owner,
        conversation_id=rows.conversation,
        title="Check result",
        criteria=[],
    )


def _assert_rls_rejects(db: Connection, statement: sa.Insert | sa.Update) -> None:
    # The nested transaction rolls back only this attempted write. The SQLSTATE distinguishes
    # a policy denial from incidental foreign-key/uniqueness violations.
    with pytest.raises(DBAPIError) as error, db.begin_nested():
        db.execute(statement)
    assert getattr(error.value.orig, "sqlstate", None) == "42501"
    assert "row-level security" in str(error.value.orig)


def test_goal_and_receipts_require_their_exact_conversation_and_owner(lab: RlsLab) -> None:
    own_id, foreign_id, attempt_id, intent_id = (uuid4() for _ in range(4))
    with lab.admin.begin() as db:
        actor_a = _actor(db, lab.tenant_a)
        own = _conversation(db, actor_a)
        same_owner = _conversation(db, actor_a)
        sibling = _conversation(db, _actor(db, lab.tenant_a))
        foreign = _conversation(db, _actor(db, lab.tenant_b))
        db.execute(_goal(foreign, foreign_id))
        # Real FK-valid but inconsistent links from older/platform writers must remain unusable.
        wrong_run = uuid4()
        db.execute(
            sa.insert(ConversationRun).values(
                id=wrong_run,
                org_id=own.org,
                tenant_id=own.tenant,
                user_id=sibling.owner,
                conversation_id=own.conversation,
                source="chat",
                status="completed",
            )
        )
    actor = sa.create_engine(lab.app_url)
    try:
        with actor.begin() as db:
            db.execute(
                sa.text("SELECT set_config('app.tenant_id', :tenant, true)"),
                {"tenant": str(own.tenant)},
            )
            db.execute(_goal(own, own_id))
            assert db.scalars(sa.select(ConversationGoal.id)).all() == [own_id]
            _assert_rls_rejects(db, _goal(sibling, uuid4()).values(user_id=own.owner))
            _assert_rls_rejects(db, _goal(sibling, uuid4()).values(org_id=own.org))
            _assert_rls_rejects(
                db, _goal(own, uuid4()).values(conversation_id=foreign.conversation)
            )
            attempt = sa.insert(GoalAttempt).values(id=attempt_id, goal_id=own_id, goal_version=1)
            intent = sa.insert(GoalTransitionIntent).values(
                id=intent_id,
                goal_id=own_id,
                goal_version=1,
                goal_revision=1,
                kind="completed",
                payload={},
            )
            for wrong_run_id in (foreign.run, sibling.run, same_owner.run, wrong_run):
                _assert_rls_rejects(db, attempt.values(run_id=wrong_run_id))
                _assert_rls_rejects(db, intent.values(run_id=wrong_run_id))
            for wrong_input in (
                sibling.queued_input,
                foreign.queued_input,
                same_owner.queued_input,
            ):
                _assert_rls_rejects(
                    db, attempt.values(run_id=own.run, followup_input_id=wrong_input)
                )
            db.execute(attempt.values(run_id=own.run, followup_input_id=own.queued_input))
            db.execute(intent.values(run_id=own.run))
            _assert_rls_rejects(
                db,
                sa.update(GoalAttempt)
                .where(GoalAttempt.id == attempt_id)
                .values(followup_input_id=sibling.queued_input),
            )
            assert db.scalars(sa.select(GoalAttempt.id)).all() == [attempt_id]
            assert db.scalars(sa.select(GoalTransitionIntent.id)).all() == [intent_id]
        with actor.begin() as db:
            db.execute(
                sa.text("SELECT set_config('app.tenant_id', :tenant, true)"),
                {"tenant": str(foreign.tenant)},
            )
            assert db.scalars(sa.select(ConversationGoal.id)).all() == [foreign_id]
            assert db.scalars(sa.select(GoalAttempt.id)).all() == []
            assert db.scalars(sa.select(GoalTransitionIntent.id)).all() == []
    finally:
        actor.dispose()
