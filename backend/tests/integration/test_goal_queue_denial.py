"""A detached worker must release its denial locks before terminal cleanup."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.authz.org_context import OrgContext
from app.config import settings
from app.database import tenant_session
from app.models import (
    Conversation,
    ConversationGoal,
    ConversationRun,
    ConversationRunInput,
    GoalTransitionIntent,
    OrganizationMember,
    TenantMember,
)
from app.services import conversation_goal_worker, conversation_run_queue_service
from app.services import conversation_run_queue_worker as queue_worker
from tests.authz.i06_rls_lab import RlsLab, async_url, rls_lab
from tests.authz.test_goal_rls_integration import _actor  # noqa: PLC2701
from tests.authz.test_goal_worker_race_integration import _terminal_run  # noqa: PLC2701
from tests.authz.test_private_queue_worker_read_order import private_selects

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(scope="module")
def lab() -> Iterator[RlsLab]:
    with rls_lab() as database:
        yield database


@pytest.mark.parametrize("goal_followup", [False, True], ids=["ordinary", "goal"])
async def test_revoked_worker_releases_locks_before_separate_cleanup(
    lab: RlsLab, monkeypatch: pytest.MonkeyPatch, goal_followup: bool
) -> None:
    with lab.admin.begin() as setup:
        tenant, org, owner, agent = _actor(setup, lab.tenant_a)
        setup.execute(sa.insert(TenantMember).values(tenant_id=tenant, user_id=owner))
        setup.execute(
            sa.insert(OrganizationMember).values(org_id=org, user_id=owner, tenant_id=tenant)
        )
    context = OrgContext(org, tenant, owner)
    engine = create_async_engine(
        async_url(lab.app_url),
        poolclass=NullPool,
        connect_args={"server_settings": {"lock_timeout": "3s"}},
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(settings, "db_rls_enabled", True)
    monkeypatch.setattr(settings, "conversation_goal_org_ids", frozenset({org}))
    monkeypatch.setattr(queue_worker, "async_session", maker)

    try:
        goal_id = None
        async with tenant_session(context, maker=maker) as db:
            if goal_followup:
                conversation_id, goal_id, predecessor = await _terminal_run(
                    lambda: tenant_session(context, maker=maker), context, agent
                )
                await conversation_goal_worker.reconcile_goal_run(db, predecessor, conversation_id)
                await db.commit()
            else:
                conversation = Conversation(
                    agent_id=agent,
                    user_id=owner,
                    org_id=org,
                    tenant_id=tenant,
                    last_activity_at=datetime.now(UTC),
                    agent_name_snapshot="Agent",
                )
                db.add(conversation)
                await db.flush()
                conversation_id = conversation.id
                await conversation_run_queue_service.enqueue_input(
                    db,
                    conversation_id=conversation_id,
                    context=context,
                    client_request_id="denied-launch",
                    source="chat",
                    input_payload={"messages": [{"role": "user", "content": "Private input"}]},
                    attachment_ids=[],
                    checkpoint_id=None,
                    priority=0,
                )
                await db.commit()
            claimed = await conversation_run_queue_service.claim_next_input(
                db, conversation_id=conversation_id, context=context
            )
            assert claimed is not None
            assert claimed.input.source == ("goal_followup" if goal_followup else "chat")
            input_id, run_id = claimed.input.id, claimed.run.id
            await db.commit()

        with lab.admin.begin() as setup:
            setup.execute(
                sa.update(OrganizationMember)
                .where(OrganizationMember.org_id == org, OrganizationMember.user_id == owner)
                .values(status="suspended")
            )
        selects: list[str] = []
        conversation_lock_pids: set[int] = set()

        def collect(conn: sa.Connection, _cursor: Any, sql: str, *_args: Any) -> None:
            if sql.lstrip().lower().startswith("select"):
                selects.append(sql)
                if "for update of conversations" in sql.lower():
                    driver = conn.connection.driver_connection
                    assert driver is not None
                    conversation_lock_pids.add(driver.get_server_pid())

        event.listen(engine.sync_engine, "before_cursor_execute", collect)
        try:
            result = await queue_worker.launch_claimed_input(input_id)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", collect)
        assert result is not None and result.id == run_id
        assert len(conversation_lock_pids) == 2
        assert not private_selects(selects)
        private_goal_fields = (
            "conversation_goals.title",
            "conversation_goals.criteria",
            "conversation_goals.last_evidence",
            "goal_attempts.evidence",
        )
        assert not any(field in sql for sql in selects for field in private_goal_fields)
        with lab.admin.begin() as inspect:
            assert (
                inspect.scalar(
                    sa.select(ConversationRun.status).where(ConversationRun.id == run_id)
                )
                == "failed"
            )
            assert (
                inspect.scalar(
                    sa.select(ConversationRunInput.status).where(
                        ConversationRunInput.id == input_id
                    )
                )
                == "failed"
            )
            if goal_id is not None:
                assert (
                    inspect.scalar(
                        sa.select(ConversationGoal.blocked_reason).where(
                            ConversationGoal.id == goal_id
                        )
                    )
                    == "permission_revoked"
                )
                assert inspect.scalars(
                    sa.select(GoalTransitionIntent.kind).where(
                        GoalTransitionIntent.goal_id == goal_id
                    )
                ).all() == ["blocked"]
    finally:
        await engine.dispose()
