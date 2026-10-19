"""F-14 (14.1, Q22): run audit rows carry the organization and the attribution split."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.models.conversation_run import ConversationRun
from app.services.conversation_audit_service import record_conversation_run_audit
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID

pytestmark = pytest.mark.asyncio
CALLER, OWNER, AGENT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _run(**attribution: object) -> ConversationRun:
    return ConversationRun(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        agent_id=AGENT,
        user_id=CALLER,
        source="chat",
        status="completed",
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        **attribution,
    )


async def test_run_audit_records_org_and_actor_subject_split(db: AsyncSession) -> None:
    run = _run(
        run_source="CHAT",
        invoker_type="user",
        resource_owner_user_id=OWNER,
        on_behalf_of_user_id=OWNER,
        credential_subject_user_id=OWNER,
    )
    user = CurrentUser(id=CALLER, email="c@f14.test", name="C")
    await record_conversation_run_audit(db, action="conversation_run.completed", run=run, user=user)
    [row] = list(db.new)
    assert (row.org_id, row.tenant_id, row.actor_user_id) == (TEST_ORG_ID, TEST_TENANT_ID, CALLER)
    assert {k: row.event_metadata[k] for k in ("actor_agent_id", "on_behalf_of_user_id")} == {
        "actor_agent_id": str(AGENT),
        "on_behalf_of_user_id": str(OWNER),
    }
    assert row.event_metadata["invoker_type"] == "user"


async def test_unattributed_runs_keep_the_old_metadata(db: AsyncSession) -> None:
    user = CurrentUser(id=CALLER, email="c@f14.test", name="C")
    await record_conversation_run_audit(
        db, action="conversation_run.completed", run=_run(), user=user
    )
    [row] = list(db.new)
    assert set(row.event_metadata) == {"run_id", "conversation_id", "agent_id", "source", "status"}
