"""H-07 (plan 8.9): a fired trigger resolves its row's company, then runs only in it."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import trigger_executor
from app.database import REASON_INFO_KEY, SCOPE_INFO_KEY
from app.models.agent_trigger import AgentTrigger
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession

TENANT_B = uuid.uuid4()


async def _trigger(db: AsyncSession, tenant_id: uuid.UUID) -> uuid.UUID:
    trigger = AgentTrigger(
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=tenant_id,
        agent_id=uuid.uuid4(),
        name="H-07 schedule",
        trigger_type="interval",
        schedule_config={"interval_minutes": 10},
        input_message="run now",
    )
    db.add(trigger)
    await db.flush()
    return trigger.id


async def test_each_trigger_fires_in_its_own_company_session(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    triggers = {await _trigger(db, TEST_TENANT_ID): TEST_TENANT_ID}
    triggers[await _trigger(db, TENANT_B)] = TENANT_B
    await db.commit()

    infos: list[dict[str, Any]] = []
    claims: list[tuple[uuid.UUID, dict[str, str]]] = []

    def maker() -> AsyncSession:
        session = TestSession()
        infos.append(session.info)
        return session

    async def fake_claim(
        session: AsyncSession, _trigger_id: str, trigger_uuid: uuid.UUID, *, force: bool
    ) -> None:
        claims.append((trigger_uuid, dict(session.info[SCOPE_INFO_KEY])))

    monkeypatch.setattr(trigger_executor, "async_session", maker)
    monkeypatch.setattr(trigger_executor, "claim_trigger_run", fake_claim)
    for trigger_id in triggers:
        assert await trigger_executor.execute_trigger(str(trigger_id)) is None

    lookups = [info for info in infos if info.get(REASON_INFO_KEY)]
    assert [info[REASON_INFO_KEY] for info in lookups] == ["scheduler.trigger_dispatch"] * 2
    assert claims == [
        (trigger_id, {"app.tenant_id": str(tenant_id), "app.user_id": str(TEST_USER_ID)})
        for trigger_id, tenant_id in triggers.items()
    ]
