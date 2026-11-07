"""Memory policy plan 15 (PR-B3, 5.6): consolidation writes no new memory sentence."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ValidationError
from app.models.audit_event import AuditEvent
from app.models.job import Job
from app.models.memory import (
    MemoryConsolidationRun,
    MemoryProposal,
    MemoryRecord,
    MemorySource,
    UserMemorySettings,
)
from app.models.notification import Notification
from app.models.organization import Tenant
from app.models.token_usage import TokenUsage
from app.services import memory_consolidation as mc
from app.services import memory_lifecycle, memory_profile, memory_service
from tests.authz.tenant_invitation_fixture import grant_tenant_admin
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID
from tests.test_memory_service import _seed_agent


async def _enable(db: AsyncSession, *, mode: str = "partial_auto") -> None:
    tenant = await db.get(Tenant, TEST_TENANT_ID)
    assert tenant is not None
    tenant.settings = {**(tenant.settings or {}), "memory": {"consolidation_enabled": True}}
    settings = await db.get(UserMemorySettings, TEST_USER_ID)
    if settings is None:
        db.add(UserMemorySettings(user_id=TEST_USER_ID, approval_mode=mode))
    else:
        settings.approval_mode, settings.consolidation_enabled = mode, True
    await db.commit()


def _record(content: str, **kw: Any) -> MemoryRecord:
    return MemoryRecord(
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        user_id=TEST_USER_ID,
        scope="user",
        content=content,
        store_path="/memories/user",
        origin=kw.pop("origin", "agent_inferred"),
        approval_channel=kw.pop("approval_channel", "auto_policy"),
        **kw,
    )


async def _add(db: AsyncSession, *records: MemoryRecord) -> list[MemoryRecord]:
    db.add_all(records)
    await db.commit()
    return list(records)


def _reply(**actions: Any) -> mc.ModelCall:
    async def call(system: str, user: str) -> tuple[str, int, int]:
        assert "기억 문장을 새로 쓰지 않습니다" in system
        return json.dumps(actions, ensure_ascii=False), 120, 30

    return call


def _sid(inputs: mc.Inputs, record: MemoryRecord) -> str:
    return next(sid for sid, row in inputs.records.items() if row.id == record.id)


@pytest.mark.asyncio
async def test_checks_keep_only_what_needs_no_new_sentence(db: AsyncSession) -> None:
    await _seed_agent(db)
    a, b, c, d, e = await _add(
        db,
        _record("회의록은 요약부터 쓴다"),
        _record("회의록은 요약부터 쓴다 (팀 규칙)"),
        _record("주간 회의는 3시에 한다"),
        _record("주간 회의는 4시에 한다"),
        _record("발표 자료는 Keynote로 만든다", origin="user_stated", approval_channel="direct"),
    )
    context = unit_context(TEST_USER_ID)
    inputs = await mc.gather(db, context, None)
    s = {row.id: _sid(inputs, row) for row in (a, b, c, d, e)}
    changes, dropped = mc.check(
        inputs,
        {
            "merge": [
                {"ids": [s[a.id], s[b.id]]},
                {"ids": [s[c.id], s[d.id]]},  # 3시 vs 4시: never merged
                {"ids": [s[a.id], s[e.id]]},  # a is reused and e is person-confirmed
            ],
            "supersede": [{"old": s[c.id], "new": s[d.id]}],
            "revoke": [{"id": s[e.id]}],  # no source message to compare with
            "profile": [
                {"op": "add", "text": "발표 자료는 Keynote로 만든다", "sources": [s[e.id]]},
                {"op": "add", "text": "발표는 5분 안에 끝낸다", "sources": [s[e.id]]},
                {"op": "add", "text": "발표 자료는 PowerPoint로 만든다", "sources": [s[e.id]]},
            ],
        },
    )
    merge = next(ch for ch in changes if ch.action == "merge")
    # HaluMem-style: the merged text is the originals joined, word for word.
    assert merge.content == f"{a.content} {b.content}" and not merge.replaces_confirmed
    supersede = next(ch for ch in changes if ch.action == "supersede")
    assert supersede.review and supersede.target is c
    profile = [ch for ch in changes if ch.action == "profile"]
    assert [ch.content for ch in profile] == ["발표 자료는 Keynote로 만든다"]
    # Rejected: the 3시/4시 merge, the reused merge, the ungrounded revoke, two lines
    # whose number or name is not in the source.
    assert dropped == 5


@pytest.mark.asyncio
async def test_partial_auto_applies_an_unmarked_merge_and_proposes_the_rest(
    db: AsyncSession,
) -> None:
    await _seed_agent(db)
    await _enable(db)
    a, b, c, d = await _add(
        db,
        _record("보고서는 표로 정리한다"),
        _record("보고서는 표로 정리한다."),
        _record("마감은 금요일이다", origin="user_stated", approval_channel="direct"),
        _record("마감은 목요일이다"),
    )
    context = unit_context(TEST_USER_ID)
    inputs = await mc.gather(db, context, None)
    s = {row.id: _sid(inputs, row) for row in (a, b, c, d)}
    outcome = await mc.run_consolidation(
        db,
        context,
        agent_id=None,
        model_call=_reply(
            merge=[{"ids": [s[a.id], s[b.id]]}],
            supersede=[{"old": s[c.id], "new": s[d.id]}],
        ),
    )
    assert (outcome.outcome, outcome.applied, outcome.proposed) == ("ok", 1, 1)
    await db.refresh(a)
    merged = await db.get(MemoryRecord, a.superseded_by_id)
    assert merged is not None and merged.status == "active"
    assert merged.content == "보고서는 표로 정리한다 보고서는 표로 정리한다."
    for row in (a, b):
        await db.refresh(row)
        assert (row.status, row.superseded_by_id) == ("superseded", merged.id)
    links = list(await db.scalars(select(MemorySource.source_record_id)))
    assert sorted(links) == sorted([a.id, b.id])
    pending = await db.scalar(select(MemoryProposal).where(MemoryProposal.status == "pending"))
    assert pending is not None and pending.action == "supersede"
    assert mc.bulk_eligible(pending) == "contradiction"
    await db.refresh(c)
    assert c.status == "active"  # a person-confirmed memory is never replaced alone
    run = await db.scalar(select(MemoryConsolidationRun))
    assert run is not None and (run.applied, run.proposed, run.tokens_in) == (1, 1, 120)
    audit = await db.scalar(
        select(AuditEvent).where(AuditEvent.action == "memory.consolidation_run")
    )
    assert audit is not None and audit.event_metadata == {
        "outcome": "ok",
        "proposed": 1,
        "applied": 1,
        "dropped": 0,
    }
    assert "보고서" not in json.dumps(audit.event_metadata, ensure_ascii=False)


@pytest.mark.asyncio
async def test_a_rejected_bundle_is_not_proposed_again(db: AsyncSession) -> None:
    await _seed_agent(db)
    await _enable(db, mode="approve_all")
    a, b = await _add(db, _record("점심은 12시에 먹는다"), _record("점심은 12시에 먹는다!"))
    context = unit_context(TEST_USER_ID)
    inputs = await mc.gather(db, context, None)
    reply = _reply(merge=[{"ids": [_sid(inputs, a), _sid(inputs, b)]}])
    first = await mc.run_consolidation(db, context, agent_id=None, model_call=reply)
    assert (first.applied, first.proposed) == (0, 1)  # approve_all: a person decides
    proposal = await db.scalar(select(MemoryProposal))
    assert proposal is not None
    await memory_service.reject_memory_proposal(db, proposal_id=proposal.id, context=context)
    again = await mc.run_consolidation(db, context, agent_id=None, model_call=reply)
    assert (again.proposed, again.dropped) == (0, 1)


@pytest.mark.asyncio
async def test_approving_carries_out_the_change_and_a_stale_one_expires(db: AsyncSession) -> None:
    await _seed_agent(db)
    await _enable(db, mode="approve_all")
    a, b, c = await _add(
        db, _record("커피는 아메리카노"), _record("커피는 아메리카노."), _record("차는 녹차")
    )
    context = unit_context(TEST_USER_ID)
    inputs = await mc.gather(db, context, None)
    await mc.run_consolidation(
        db,
        context,
        agent_id=None,
        model_call=_reply(
            merge=[{"ids": [_sid(inputs, a), _sid(inputs, b)]}],
            expire=[{"id": _sid(inputs, c)}],
        ),
    )
    merge = await db.scalar(select(MemoryProposal).where(MemoryProposal.action == "merge"))
    expire = await db.scalar(select(MemoryProposal).where(MemoryProposal.action == "expire"))
    assert merge is not None and expire is not None
    with pytest.raises(ValidationError) as caught:
        await memory_service.approve_memory_proposal(
            db, proposal_id=merge.id, context=context, content="바꾼 문장"
        )
    assert caught.value.code == "MEMORY_PROPOSAL_NOT_EDITABLE"
    done = await memory_service.approve_memory_proposal(db, proposal_id=merge.id, context=context)
    assert done is not None and done[1] is not None
    assert done[1].content == "커피는 아메리카노 커피는 아메리카노."
    await memory_lifecycle.revoke(db, context=context, memory_id=c.id, reason=None)
    with pytest.raises(ValidationError) as stale:
        await memory_service.approve_memory_proposal(db, proposal_id=expire.id, context=context)
    assert stale.value.code == "MEMORY_PROPOSAL_STALE"
    await db.refresh(expire)
    assert expire.status == "expired"


@pytest.mark.asyncio
async def test_a_profile_line_is_proposed_with_its_source_and_applied_on_approval(
    db: AsyncSession,
) -> None:
    await _seed_agent(db)
    await _enable(db)  # partial_auto: profile updates still need a person
    (a,) = await _add(db, _record("항상 존댓말로 답해 달라고 했다"))
    context = unit_context(TEST_USER_ID)
    inputs = await mc.gather(db, context, None)
    await mc.run_consolidation(
        db,
        context,
        agent_id=None,
        model_call=_reply(
            profile=[{"op": "add", "text": "존댓말로 답한다", "sources": [_sid(inputs, a)]}]
        ),
    )
    proposal = await db.scalar(select(MemoryProposal).where(MemoryProposal.action == "profile"))
    assert proposal is not None and proposal.status == "pending"
    assert mc.bulk_eligible(proposal) is None
    await memory_service.approve_memory_proposal(db, proposal_id=proposal.id, context=context)
    view = await memory_profile.view(db, context, "user_profile", None)
    assert [(ln.text, ln.origin, ln.sources) for ln in view.lines] == [
        ("존댓말로 답한다", "consolidation", (str(a.id),))
    ]


@pytest.mark.asyncio
async def test_off_for_the_company_or_the_person_runs_nothing(db: AsyncSession) -> None:
    await _seed_agent(db)  # a company tenant: consolidation off by default (Q3)
    await _add(db, _record("하나"), _record("둘"))
    context = unit_context(TEST_USER_ID)
    skipped = await mc.run_consolidation(db, context, agent_id=None, model_call=_reply())
    assert skipped.outcome == "skipped"
    await _enable(db)
    settings = await db.get(UserMemorySettings, TEST_USER_ID)
    assert settings is not None
    settings.consolidation_enabled = False
    await db.commit()
    assert (
        await mc.run_consolidation(db, context, agent_id=None, model_call=_reply())
    ).outcome == "skipped"
    assert await db.scalar(select(func.count()).select_from(MemoryConsolidationRun)) == 0


@pytest.mark.asyncio
async def test_three_failures_stop_the_scope_and_notify_once(db: AsyncSession) -> None:
    await _seed_agent(db)
    await _enable(db)
    await _add(db, _record("하나"), _record("둘"))
    context = unit_context(TEST_USER_ID)

    async def broken(system: str, user: str) -> tuple[str, int, int]:
        return "not json", 1, 1

    for _ in range(3):
        assert (
            await mc.run_consolidation(db, context, agent_id=None, model_call=broken)
        ).outcome == "failed"
    assert (
        await mc.run_consolidation(db, context, agent_id=None, model_call=broken)
    ).outcome == "skipped"
    notes = list(
        await db.scalars(
            select(Notification).where(Notification.kind == "memory.consolidation_stopped")
        )
    )
    assert len(notes) == 1 and notes[0].user_id == TEST_USER_ID
    # Saving more memories does not queue a stopped scope again.
    for i in range(6):
        await memory_service.create_memory_record(
            db,
            context=context,
            payload=memory_service.MemoryRecordCreate(scope="user", content=f"새 기억 {i}"),
        )
    assert await db.scalar(select(Job).where(Job.type == mc.CONSOLIDATE_JOB)) is None


@pytest.mark.asyncio
async def test_five_new_memories_queue_one_run(db: AsyncSession) -> None:
    await _seed_agent(db)
    await _enable(db)
    context = unit_context(TEST_USER_ID)
    for i in range(5):
        await memory_service.create_memory_record(
            db,
            context=context,
            payload=memory_service.MemoryRecordCreate(scope="user", content=f"기억 {i}"),
        )
        jobs = list(await db.scalars(select(Job).where(Job.type == mc.CONSOLIDATE_JOB)))
        assert len(jobs) == (1 if i == 4 else 0)
    assert jobs[0].payload == {
        "user_id": str(TEST_USER_ID),
        "org_id": str(TEST_ORG_ID),
        "agent_id": None,
    }


@pytest.mark.asyncio
async def test_the_job_books_job_usage_without_a_conversation(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_agent(db)
    await _enable(db)
    await _add(db, _record("하나"), _record("둘"))
    call = _reply()
    call.model_name = "gpt-test"  # type: ignore[attr-defined]

    async def fake_model(context):
        return call

    flushed: list = []

    async def fake_flush(tenant_id, entries, session_factory=None):
        flushed.extend(entries)

    monkeypatch.setattr(mc, "system_model_call", fake_model)
    monkeypatch.setattr("app.services.spend_flush.flush_tenant", fake_flush)
    from app.jobs.context import JobContext
    from tests.sqlite_test_engine import TestSession

    async def progress(percent: int, note: str | None) -> None:
        return None

    result = await mc._run_job(  # noqa: SLF001
        JobContext(
            job_id=uuid.uuid4(),
            tenant_id=TEST_TENANT_ID,
            org_id=TEST_ORG_ID,
            actor_id=None,
            type=mc.CONSOLIDATE_JOB,
            attempt=1,
            payload={"user_id": str(TEST_USER_ID), "org_id": str(TEST_ORG_ID), "agent_id": None},
            session=TestSession,
            _progress=progress,
        )
    )
    assert result["outcome"] == "ok"
    usage = await db.scalar(select(TokenUsage))
    assert usage is not None
    assert (usage.conversation_id, usage.invoker_type, usage.total_tokens) == (None, "job", 150)
    assert [(e.user_id, e.tokens_in) for e in flushed] == [(TEST_USER_ID, 120)]


@pytest.mark.asyncio
async def test_bulk_approval_skips_what_needs_its_own_decision(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _seed_agent(db)
    await _enable(db, mode="approve_all")
    a, b, c, d = await _add(
        db,
        _record("우유는 저지방"),
        _record("우유는 저지방."),
        _record("출근은 9시"),
        _record("출근은 10시"),
    )
    context = unit_context(TEST_USER_ID)
    inputs = await mc.gather(db, context, None)
    await mc.run_consolidation(
        db,
        context,
        agent_id=None,
        model_call=_reply(
            merge=[{"ids": [_sid(inputs, a), _sid(inputs, b)]}],
            supersede=[{"old": _sid(inputs, c), "new": _sid(inputs, d)}],
        ),
    )
    listed = await client.get("/api/memory-proposals")
    body = {row["action"]: row for row in listed.json()}
    assert body["merge"]["bulk_block"] is None
    assert [s["content"] for s in body["merge"]["sources"]] == ["우유는 저지방", "우유는 저지방."]
    assert body["supersede"]["bulk_block"] == "contradiction"
    result = await client.post(
        "/api/memory-proposals/bulk-approve",
        json={"ids": [body["merge"]["id"], body["supersede"]["id"]]},
    )
    assert result.status_code == 200, result.text
    assert result.json()["approved"] == [body["merge"]["id"]]
    assert result.json()["skipped"] == [{"id": body["supersede"]["id"], "reason": "contradiction"}]


@pytest.mark.asyncio
async def test_the_company_memory_policy_needs_an_admin(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _seed_agent(db)
    shown = await client.get("/api/me/memory-tenant-policy")
    assert shown.status_code == 200, shown.text
    body = shown.json()
    assert body["tenant_kind"] == "company" and body["can_manage"] is False
    assert body["values"]["consolidation_enabled"] is False  # company default (Q3)
    assert body["defaults"]["default_mode"] == "approve_all"
    change = {**body["values"], "consolidation_enabled": True}
    assert (await client.put("/api/me/memory-tenant-policy", json=change)).status_code == 403
    await grant_tenant_admin(db, TEST_TENANT_ID, TEST_USER_ID)
    saved = await client.put("/api/me/memory-tenant-policy", json=change)
    assert saved.status_code == 200, saved.text
    assert saved.json()["values"]["consolidation_enabled"] is True
    wrong = {**change, "default_mode": "auto_notify", "allowed_modes": ["approve_all"]}
    refused = await client.put("/api/me/memory-tenant-policy", json=wrong)
    assert refused.status_code == 422
    event = await db.scalar(
        select(AuditEvent).where(AuditEvent.action == "memory.tenant_policy_update")
    )
    assert event is not None and event.event_metadata == {"changed": ["consolidation_enabled"]}
    assert (event.target_type, str(event.target_id)) == ("tenant", str(TEST_TENANT_ID))


@pytest.mark.asyncio
async def test_person_confirmed_memories_are_never_merged_alone_or_with_agent_ones(
    db: AsyncSession,
) -> None:
    await _seed_agent(db)
    await _enable(db)  # partial_auto
    human = {"origin": "user_stated", "approval_channel": "direct"}
    a, b, c = await _add(
        db,
        _record("커피는 라떼를 마신다", **human),
        _record("커피는 라떼를 마신다.", **human),
        _record("커피는 라떼를 마신다!"),
    )
    context = unit_context(TEST_USER_ID)
    inputs = await mc.gather(db, context, None)
    _changes, dropped = mc.check(inputs, {"merge": [{"ids": [_sid(inputs, a), _sid(inputs, c)]}]})
    assert dropped == 1  # a person's memory and an agent's are not one bundle
    outcome = await mc.run_consolidation(
        db,
        context,
        agent_id=None,
        model_call=_reply(merge=[{"ids": [_sid(inputs, a), _sid(inputs, b)]}]),
    )
    assert (outcome.applied, outcome.proposed) == (0, 1)
    proposal = await db.scalar(select(MemoryProposal))
    assert proposal is not None and mc.bulk_eligible(proposal) == "replaces_confirmed"
    for row in (a, b):
        await db.refresh(row)
        assert row.status == "active"


@pytest.mark.asyncio
async def test_scheduling_never_commits_or_loses_the_memory_being_saved(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It runs inside the save: no settings insert, and a failure only skips scheduling."""
    await _seed_agent(db)
    tenant = await db.get(Tenant, TEST_TENANT_ID)
    assert tenant is not None
    tenant.settings = {"memory": {"consolidation_enabled": True}}
    await db.commit()
    context = unit_context(TEST_USER_ID)
    assert await db.get(UserMemorySettings, TEST_USER_ID) is None
    record = _record("저장 중인 기억")
    db.add(record)
    await db.flush()
    await mc.maybe_schedule(db, context, record)
    assert await db.get(UserMemorySettings, TEST_USER_ID) is None  # read, not inserted

    async def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("scheduling down")

    monkeypatch.setattr(mc, "_schedule", broken)
    saved = await memory_service.create_memory_record(
        db,
        context=context,
        payload=memory_service.MemoryRecordCreate(scope="user", content="그래도 저장된다"),
    )
    assert saved is not None and saved.content == "그래도 저장된다"
