"""I-06 checkpoint metadata cross-check on real PostgreSQL (lane only).

The real ``PostgresSaver`` writes checkpoints whose metadata carries the run's
company; the audit, run as the non-owner app role under RLS, finds the thread
written under another company and ignores threads with no conversation.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import database
from app.config import settings
from app.database import platform_session
from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.tenancy.checkpoint_audit import audit_checkpoint_tenants, checkpoint_tenant
from tests.authz.i06_rls_lab import RlsLab, async_url, rls_lab
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, ensure_unit_scope

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def lab() -> Iterator[RlsLab]:
    with rls_lab() as value:
        yield value


@pytest.fixture(autouse=True)
def rls_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "db_rls_enabled", True)


async def _conversations(lab: RlsLab, count: int) -> list[UUID]:
    owner = create_async_engine(async_url(lab.admin_url))
    try:
        maker = async_sessionmaker(owner, class_=database.ShieldedAsyncSession)
        async with platform_session("seed.startup", maker=maker) as db:
            context = await ensure_unit_scope(db, CurrentUser(id=uuid4(), email="a@b.c", name="A"))
            model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
            db.add(model)
            await db.flush()
            agent = Agent(
                org_id=TEST_ORG_ID,
                tenant_id=TEST_TENANT_ID,
                user_id=context.user_id,
                name="Agent",
                system_prompt="x",
                model_id=model.id,
            )
            db.add(agent)
            await db.flush()
            rows = [
                Conversation(
                    agent_id=agent.id,
                    title="c",
                    user_id=context.user_id,
                    org_id=TEST_ORG_ID,
                    tenant_id=TEST_TENANT_ID,
                    agent_name_snapshot=agent.name,
                    last_activity_at=datetime.now(UTC),
                )
                for _ in range(count)
            ]
            db.add_all(rows)
            await db.flush()
            ids = [row.id for row in rows]  # read before commit expires the rows
            await db.commit()
            return ids
    finally:
        await owner.dispose()


def _write(lab: RlsLab, writes: list[tuple[str, UUID]]) -> None:
    url = lab.admin_url.set(drivername="postgresql").render_as_string(hide_password=False)
    with PostgresSaver.from_conn_string(url) as saver:
        saver.setup()
        for thread, tenant in writes:
            configurable = {"thread_id": thread, "checkpoint_ns": "", **checkpoint_tenant(tenant)}
            saver.put({"configurable": configurable}, empty_checkpoint(), {"step": -1}, {})


async def test_audit_finds_a_thread_written_under_another_company(lab: RlsLab) -> None:
    good, bad = await _conversations(lab, 2)
    _write(
        lab, [(str(good), TEST_TENANT_ID), (str(bad), lab.tenant_b), ("builder-x", lab.tenant_a)]
    )
    engine = create_async_engine(async_url(lab.app_url))
    try:
        maker = async_sessionmaker(engine, class_=database.ShieldedAsyncSession)
        async with platform_session("checkpoint.tenant_audit", maker=maker) as db:
            report = await audit_checkpoint_tenants(db, page=1)
    finally:
        await engine.dispose()
    assert report.mismatched_threads == [str(bad)]
    assert (report.threads, report.mismatched, report.orphaned) == (3, 1, 1)
