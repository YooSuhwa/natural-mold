"""Memory policy plan 15 (PR-B1, 5.7): revoke, restore, real delete, export, retention."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime.memory_context import load_memory_context_in
from app.agent_runtime.runtime_config import AgentConfig
from app.exceptions import ValidationError
from app.models.audit_event import AuditEvent
from app.models.memory import (
    MemoryErasure,
    MemoryProposal,
    MemoryRecord,
    MemoryRevision,
    MemorySource,
)
from app.schemas.retention import experience_days
from app.services import memory_lifecycle, memory_writes
from app.services.memory_service import MemoryShape
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID
from tests.test_memory_service import _seed_agent

TEXT = "회의록은 항상 요약부터 써 주세요"


async def _save(db: AsyncSession, content: str = TEXT, **shape) -> MemoryRecord:
    result = await memory_writes.write_memory(
        db,
        context=unit_context(TEST_USER_ID),
        write=memory_writes.MemoryWrite(scope="user", content=content, shape=MemoryShape(**shape)),
    )
    assert result.record is not None
    return result.record


def _cfg(agent_id: uuid.UUID) -> AgentConfig:
    return AgentConfig(
        org_context=unit_context(TEST_USER_ID),
        provider="openai",
        model_name="gpt-4o",
        api_key=None,
        base_url=None,
        system_prompt="Hi",
        tools_config=[],
        thread_id="t",
        agent_id=str(agent_id),
        user_id=str(TEST_USER_ID),
    )


@pytest.mark.asyncio
async def test_a_revoked_memory_leaves_the_prompt_and_comes_back_on_restore(
    db: AsyncSession,
) -> None:
    agent_id, _ = await _seed_agent(db)
    record = await _save(db)
    context = unit_context(TEST_USER_ID)
    assert await memory_lifecycle.revoke(db, context=context, memory_id=record.id, reason="틀림")
    prompt, _ = await load_memory_context_in(_cfg(agent_id), db)
    assert TEXT not in prompt
    revoked = await db.get(MemoryRecord, record.id)
    assert revoked is not None and revoked.content == TEXT  # kept for a second look
    restored = await memory_lifecycle.restore(db, context=context, memory_id=record.id)
    assert restored is not None and restored.status == "active"
    prompt, _ = await load_memory_context_in(_cfg(agent_id), db)
    assert TEXT in prompt
    actions = [
        r.action
        for r in await db.scalars(select(MemoryRevision).order_by(MemoryRevision.created_at))
    ]
    assert actions == ["create", "revoke", "restore"]


@pytest.mark.asyncio
async def test_a_delete_reaches_revisions_proposals_and_derived_memories(
    db: AsyncSession,
) -> None:
    await _seed_agent(db)
    context = unit_context(TEST_USER_ID)
    record = await _save(db)
    derived = MemoryRecord(
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        user_id=TEST_USER_ID,
        scope="user",
        content="요약부터 쓰는 회의록 규칙",
        store_path="/memories/user",
        origin="consolidation",
    )
    db.add(derived)
    await db.flush()
    db.add(
        MemorySource(
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            record_id=derived.id,
            source_record_id=record.id,
        )
    )
    proposal = MemoryProposal(
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        user_id=TEST_USER_ID,
        scope="user",
        content=TEXT,
        status="approved",
        record_id=record.id,
    )
    pending_copy = MemoryProposal(
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        user_id=TEST_USER_ID,
        scope="user",
        content=TEXT + " ",
        status="pending",
    )
    db.add_all([proposal, pending_copy])
    await db.commit()

    receipt = await memory_lifecycle.delete_one(db, context=context, memory_id=record.id)

    assert receipt is not None
    assert (receipt.deleted, receipt.revisions_removed, receipt.proposals_cleared) == (1, 1, 2)
    assert receipt.derived_marked == 1 and "conversation_history" in receipt.kept_elsewhere
    assert await db.get(MemoryRecord, record.id) is None
    assert (
        await db.scalar(select(MemoryRevision).where(MemoryRevision.record_id == record.id)) is None
    )
    for row in (proposal, pending_copy):
        await db.refresh(row)
        assert row.content == "" and row.flags == {"erased": True}
    assert pending_copy.status == "rejected"
    await db.refresh(derived)
    assert derived.flags.get("source_deleted") is True
    assert await db.scalar(select(MemoryErasure.content_digest)) is not None
    assert TEXT not in str([row.event_metadata for row in await db.scalars(select(AuditEvent))])


@pytest.mark.asyncio
async def test_a_scope_delete_needs_the_count_the_person_saw(db: AsyncSession) -> None:
    await _seed_agent(db)
    context = unit_context(TEST_USER_ID)
    await _save(db)
    await _save(db, "두 번째 기억")
    with pytest.raises(ValidationError):
        await memory_lifecycle.delete_scope(
            db, context=context, scope="all", agent_id=None, confirm_count=1
        )
    receipt = await memory_lifecycle.delete_scope(
        db, context=context, scope="all", agent_id=None, confirm_count=2
    )
    assert receipt.deleted == 2
    assert await db.scalar(select(MemoryRecord.id)) is None
    bulk = await db.scalar(
        select(AuditEvent).where(AuditEvent.action == "memory.record_delete_bulk")
    )
    assert bulk is not None and bulk.event_metadata is not None
    assert (bulk.event_metadata["deleted"], bulk.event_metadata["scope"]) == (2, "all")


@pytest.mark.asyncio
async def test_export_holds_only_the_persons_memories(db: AsyncSession) -> None:
    await _seed_agent(db)
    context = unit_context(TEST_USER_ID)
    record = await _save(db, kind="preference", topic="회의록")
    db.add(
        MemoryRecord(
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            user_id=uuid.uuid4(),
            scope="user",
            content="다른 사람 기억",
            store_path="/memories/user",
        )
    )
    await db.commit()
    body = await memory_lifecycle.export(db, context=context)
    assert [item["id"] for item in body["records"]] == [str(record.id)]
    assert body["records"][0]["revisions"][0]["after"] == TEXT
    markdown = memory_lifecycle.export_markdown(body)
    assert "### 선호" in markdown and f"- [회의록] {TEXT}" in markdown
    assert "다른 사람" not in json.dumps(body, ensure_ascii=False)
    event = await db.scalar(select(AuditEvent).where(AuditEvent.action == "memory.export"))
    assert event is not None and event.event_metadata == {
        "scope": "all",
        "records": 1,
        "proposals": 0,
    }


@pytest.mark.asyncio
async def test_retention_removes_old_experience_and_long_revoked_memories(
    db: AsyncSession,
) -> None:
    await _seed_agent(db)
    context = unit_context(TEST_USER_ID)
    now = datetime.now(UTC)
    old = await _save(db, "작년 워크숍 다녀옴", kind="experience")
    recent = await _save(db, "어제 회의 다녀옴", kind="experience")
    fact = await _save(db, "작년에 정한 사실")
    revoked = await _save(db, "철회된 기억")
    await memory_lifecycle.revoke(db, context=context, memory_id=revoked.id, reason=None)
    old.occurred_end = (now - timedelta(days=400)).replace(tzinfo=None)
    fact.created_at = (now - timedelta(days=400)).replace(tzinfo=None)
    row = await db.get(MemoryRecord, revoked.id)
    assert row is not None
    row.revoked_at = (now - timedelta(days=31)).replace(tzinfo=None)
    await db.commit()

    removed = await memory_lifecycle.purge_expired(
        db, org_id=TEST_ORG_ID, tenant_id=TEST_TENANT_ID, experience_days=365, now=now
    )

    assert removed == 2
    left = set(await db.scalars(select(MemoryRecord.id)))
    assert left == {recent.id, fact.id}
    assert await db.scalar(select(MemoryErasure.id)) is None  # expiry is not an erasure


@pytest.mark.parametrize(
    ("stored", "kind", "expected"),
    [
        (None, "company", 365),
        (None, "personal", None),
        (90, "personal", 90),
        ("none", "company", None),
    ],
)
def test_experience_period_defaults_by_tenant_kind(stored, kind, expected) -> None:
    settings: dict[str, Any] = (
        {}
        if stored is None
        else {"retention": {"experience_memory_days": None if stored == "none" else stored}}
    )
    assert experience_days(settings, kind) == expected


@pytest.mark.asyncio
async def test_restore_runs_the_checks_again(db: AsyncSession) -> None:
    """A memory kept before the checks existed (or under older rules) is checked on restore."""
    await _seed_agent(db)
    legacy = MemoryRecord(
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        user_id=TEST_USER_ID,
        scope="user",
        content="주민번호 900101-1234568",
        store_path="/memories/user",
        status="revoked",
    )
    db.add(legacy)
    await db.commit()
    with pytest.raises(ValidationError):
        await memory_lifecycle.restore(db, context=unit_context(TEST_USER_ID), memory_id=legacy.id)


@pytest.mark.asyncio
async def test_a_legacy_memory_the_checks_refuse_never_reaches_a_run(db: AsyncSession) -> None:
    agent_id, _ = await _seed_agent(db)
    db.add(
        MemoryRecord(
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            user_id=TEST_USER_ID,
            scope="user",
            content="legacy blocked 3141",
            store_path="/memories/user",
            flags={"legacy_rejected": "MEMORY_PII_RRN"},
        )
    )
    await db.commit()
    prompt, _ = await load_memory_context_in(_cfg(agent_id), db)
    assert "legacy blocked 3141" not in prompt
