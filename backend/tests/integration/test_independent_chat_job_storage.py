"""Prove handoff uniqueness and tenant isolation against the migrated PostgreSQL schema."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.config import settings
from app.models import Agent, Conversation, ConversationRun, ConversationRunAgent, Model, User
from app.models.independent_chat_job import IndependentChatJob
from app.models.organization import Organization
from tests.authz.i06_rls_lab import BACKEND, RlsLab, async_url, rls_lab

REVISION = "i49_independent_chat_jobs"


@pytest.fixture(scope="module")
def lab() -> Iterator[RlsLab]:
    with rls_lab() as value:
        yield value


@dataclass(frozen=True)
class Handoffs:
    parent: UUID
    child: UUID
    first: UUID
    second: UUID
    user: UUID
    first_run: UUID
    second_run: UUID


def _seed(db: Session, tenant: UUID) -> Handoffs:
    org = Organization(tenant_id=tenant, name="Team", slug=uuid4().hex)
    user = User(name="Tester", email=f"{uuid4().hex}@job.test")
    model = Model(provider="openai", model_name="test", display_name="Test")
    db.add_all([user, org, model])
    db.flush()
    scope = {"tenant_id": tenant, "org_id": org.id, "user_id": user.id}
    parent, child = [
        Agent(name=name, model_id=model.id, system_prompt="Test", **scope)
        for name in ("Parent", "Child")
    ]
    db.add_all([parent, child])
    db.flush()

    def accepted(agent: Agent, source: str) -> tuple[UUID, UUID]:
        conversation = Conversation(
            agent_id=agent.id,
            agent_name_snapshot=agent.name,
            last_activity_at=datetime.now(UTC),
            source=source,
            **scope,
        )
        db.add(conversation)
        db.flush()
        run = ConversationRun(
            conversation_id=conversation.id,
            agent_id=agent.id,
            source="chat",
            status="completed",
            is_active=False,
            **scope,
        )
        db.add(run)
        db.flush()
        db.add(
            ConversationRunAgent(
                run_id=run.id, agent_id=agent.id, agent_id_snapshot=agent.id, role="root", path=[]
            )
        )
        return conversation.id, run.id

    parent_conv, parent_run = accepted(parent, "ui")
    db.add(
        ConversationRunAgent(
            run_id=parent_run,
            agent_id=child.id,
            agent_id_snapshot=child.id,
            role="subagent",
            path=[str(child.id)],
        )
    )
    jobs = []
    for _ in range(2):
        child_conv, child_run = accepted(child, "independent_job")
        job = IndependentChatJob(
            parent_agent_id=parent.id,
            child_agent_id=child.id,
            parent_conversation_id=parent_conv,
            child_conversation_id=child_conv,
            parent_run_id=parent_run,
            child_run_id=child_run,
            request_id=uuid4(),
            request_digest="a" * 64,
            capability_sha256="b" * 64,
            **scope,
        )
        db.add(job)
        db.flush()
        jobs.append(job)
    return Handoffs(
        parent=parent_conv,
        child=jobs[0].child_conversation_id,
        first=jobs[0].id,
        second=jobs[1].id,
        user=user.id,
        first_run=jobs[0].child_run_id,
        second_run=jobs[1].child_run_id,
    )


def test_native_uniqueness_and_parent_deletion(lab: RlsLab) -> None:
    with Session(lab.admin) as db, db.begin():
        handoffs = _seed(db, lab.tenant_a)
        first = db.get(IndependentChatJob, handoffs.first)
        assert first is not None
        first.source_child_task_id = uuid4()
        db.flush()

        # Initially both rows had NULL source IDs. A real source and each stable
        # handoff identity are single-use, regardless of the client process.
        for field, constraint in (
            ("request_id", "uq_independent_chat_jobs_request"),
            ("source_child_task_id", "uq_independent_chat_jobs_source_task"),
            ("child_run_id", "uq_independent_chat_jobs_child_run"),
            ("child_conversation_id", "uq_independent_chat_jobs_child_conversation"),
        ):
            with pytest.raises(IntegrityError, match=constraint), db.begin_nested():
                db.execute(
                    sa.update(IndependentChatJob)
                    .where(IndependentChatJob.id == handoffs.second)
                    .values({field: getattr(first, field)})
                )
        with (
            pytest.raises(IntegrityError, match="fk_independent_chat_jobs_parent_root"),
            db.begin_nested(),
        ):
            db.execute(
                sa.update(IndependentChatJob)
                .where(IndependentChatJob.id == handoffs.second)
                .values(parent_agent_id=uuid4())
            )

        db.execute(sa.delete(Conversation).where(Conversation.id == handoffs.parent))
        assert (
            db.scalar(
                sa.select(sa.func.count())
                .select_from(IndependentChatJob)
                .where(IndependentChatJob.id.in_([handoffs.first, handoffs.second]))
            )
            == 0
        )
        assert (
            db.scalar(sa.select(Conversation.source).where(Conversation.id == handoffs.child))
            == "independent_job"
        )


def test_rls_binds_handoff_to_both_visible_runs(lab: RlsLab) -> None:
    with Session(lab.admin) as db, db.begin():
        a, b = _seed(db, lab.tenant_a), _seed(db, lab.tenant_b)
    app = sa.create_engine(lab.app_url)
    try:
        with app.begin() as db:
            db.execute(
                sa.text("SELECT set_config('app.tenant_id', :tenant, true)"),
                {"tenant": str(lab.tenant_a)},
            )
            db.execute(
                sa.text("SELECT set_config('app.user_id', :user, true)"), {"user": str(a.user)}
            )
            visible = db.scalars(
                sa.select(IndependentChatJob.id).where(
                    IndependentChatJob.id.in_([a.first, b.first])
                )
            ).all()
            assert visible == [a.first]
            linked = db.scalar(
                sa.update(IndependentChatJob)
                .where(IndependentChatJob.id == a.first)
                .values(pending_approval_run_id=a.first_run)
                .returning(IndependentChatJob.pending_approval_run_id)
            )
            assert linked == a.first_run
            for field, foreign in (
                ("parent_conversation_id", b.parent),
                ("user_id", b.user),
                ("pending_approval_run_id", b.first_run),
                ("pending_approval_run_id", a.second_run),
            ):
                with pytest.raises(DBAPIError) as denied, db.begin_nested():
                    db.execute(
                        sa.update(IndependentChatJob)
                        .where(IndependentChatJob.id == a.first)
                        .values({field: foreign})
                    )
                assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    finally:
        app.dispose()


def test_migration_recreates_model_constraints(
    lab: RlsLab, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.attributes["database_url"] = async_url(lab.admin_url)
    monkeypatch.setattr(settings, "db_rls_enabled", True)
    monkeypatch.setattr(settings, "database_url", async_url(lab.app_url))
    monkeypatch.setattr(settings, "migration_database_url", async_url(lab.admin_url))
    revision = ScriptDirectory.from_config(config).get_revision(REVISION)
    assert revision is not None and isinstance(revision.down_revision, str)
    table = IndependentChatJob.__table__
    with Session(lab.admin) as db, db.begin():
        # This database belongs to this test module. Isolate the orphan case
        # from any still-linked children created by earlier tests.
        db.execute(sa.delete(Conversation).where(Conversation.source == "independent_job"))
        existing = _seed(db, lab.tenant_a)
        db.execute(sa.delete(Conversation).where(Conversation.id == existing.parent))
        assert db.scalar(sa.select(sa.func.count()).select_from(IndependentChatJob)) == 0
        assert db.scalar(
            sa.select(Conversation.source).where(Conversation.id == existing.child)
        ) == ("independent_job")
    with pytest.raises(RuntimeError, match="Cannot downgrade while independent child"):
        command.downgrade(config, revision.down_revision)
    assert sa.inspect(lab.admin).has_table(table.name)
    # The lab owns an isolated database. Remove the test's child conversations
    # before checking that an empty installation can be rolled back.
    with lab.admin.begin() as db:
        db.execute(sa.delete(Conversation).where(Conversation.source == "independent_job"))
    command.downgrade(config, revision.down_revision)
    assert not sa.inspect(lab.admin).has_table(table.name)
    command.upgrade(config, "head")
    inspector = sa.inspect(lab.admin)
    assert {column["name"]: column["nullable"] for column in inspector.get_columns(table.name)} == {
        column.name: column.nullable for column in table.columns
    }
    assert {fk["name"] for fk in inspector.get_foreign_keys(table.name)} == {
        fk.name for fk in table.foreign_key_constraints
    }
    assert {constraint["name"] for constraint in inspector.get_unique_constraints(table.name)} == {
        c.name for c in table.constraints if isinstance(c, sa.UniqueConstraint)
    }
