"""Phase 3 skill-axis usage ledger — service aggregation + API contract."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill_usage_event import SkillUsageEvent
from app.services import skill_usage_service
from app.services.skill_usage_service import (
    get_skill_usage_summary,
    record_chat_execution,
    record_evaluation_usage,
)
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID, TestSession, make_user

pytestmark = pytest.mark.asyncio


def _skill_content(slug: str) -> str:
    return (
        "---\n"
        f"name: {slug}\n"
        'description: "Use when testing skill usage accounting."\n'
        "---\n\n"
        "Use when testing usage accounting.\n"
    )


async def _create_skill(db: AsyncSession, tmp_path: Path, *, user_id=TEST_USER_ID, slug="usage"):
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=user_id,
            name=slug.title(),
            slug=slug,
            description="Use when testing skill usage accounting.",
            content=_skill_content(slug),
            version="1.0.0",
        )
        await db.commit()
        return skill


# ---------------------------------------------------------------------------
# Service — recording + aggregation
# ---------------------------------------------------------------------------


async def test_summary_aggregates_sources_and_pricing(db: AsyncSession, tmp_path: Path) -> None:
    skill = await _create_skill(db, tmp_path)

    await record_evaluation_usage(
        db,
        skill_id=skill.id,
        user_id=TEST_USER_ID,
        evaluation_run_id=None,  # SET NULL semantics — run may be pruned later
        model_name="gpt-5.4",
        tokens_in=1000,
        tokens_out=200,
        cost_usd=Decimal("0.012345"),
    )
    await record_evaluation_usage(
        db,
        skill_id=skill.id,
        user_id=TEST_USER_ID,
        evaluation_run_id=None,
        model_name="unpriced-model",
        tokens_in=500,
        tokens_out=100,
        cost_usd=None,
    )
    await record_chat_execution(
        db,
        skill_id=skill.id,
        user_id=TEST_USER_ID,
        conversation_id=None,
        agent_id=None,
    )
    await record_chat_execution(
        db,
        skill_id=skill.id,
        user_id=TEST_USER_ID,
        conversation_id=None,
        agent_id=None,
    )
    await db.commit()

    summary = await get_skill_usage_summary(db, skill_id=skill.id, days=30)

    assert summary.tokens_in == 1500
    assert summary.tokens_out == 300
    assert summary.cost_usd == pytest.approx(0.012345)
    assert summary.priced_event_count == 1
    assert summary.unpriced_token_event_count == 1
    assert summary.evaluation_run_count == 2
    assert summary.chat_execution_count == 2
    assert len(summary.daily) == 1
    day = summary.daily[0]
    assert day.tokens_in == 1500
    assert day.execution_count == 2


async def test_summary_window_excludes_old_events(db: AsyncSession, tmp_path: Path) -> None:
    skill = await _create_skill(db, tmp_path)
    old = await record_chat_execution(
        db,
        skill_id=skill.id,
        user_id=TEST_USER_ID,
        conversation_id=None,
        agent_id=None,
    )
    old.created_at = old.created_at - timedelta(days=45)
    await record_chat_execution(
        db,
        skill_id=skill.id,
        user_id=TEST_USER_ID,
        conversation_id=None,
        agent_id=None,
    )
    await db.commit()

    summary = await get_skill_usage_summary(db, skill_id=skill.id, days=30)
    assert summary.chat_execution_count == 1

    wide = await get_skill_usage_summary(db, skill_id=skill.id, days=60)
    assert wide.chat_execution_count == 2


async def test_summary_scopes_to_skill(db: AsyncSession, tmp_path: Path) -> None:
    skill_a = await _create_skill(db, tmp_path, slug="usage-a")
    skill_b = await _create_skill(db, tmp_path, slug="usage-b")
    await record_chat_execution(
        db,
        skill_id=skill_a.id,
        user_id=TEST_USER_ID,
        conversation_id=None,
        agent_id=None,
    )
    await db.commit()

    summary_b = await get_skill_usage_summary(db, skill_id=skill_b.id, days=30)
    assert summary_b.chat_execution_count == 0
    assert summary_b.tokens_in == 0


# ---------------------------------------------------------------------------
# Non-fatal chat recorder (own session — executor hot path)
# ---------------------------------------------------------------------------


async def test_record_chat_execution_nonfatal_writes_event(
    db: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(skill_usage_service, "async_session", TestSession)
    skill = await _create_skill(db, tmp_path)
    conversation_id = uuid.uuid4()

    await skill_usage_service.record_chat_execution_nonfatal(
        skill_id=skill.id,
        user_id=TEST_USER_ID,
        thread_id=str(conversation_id),
        agent_id=None,
    )

    rows = (
        (await db.execute(select(SkillUsageEvent).where(SkillUsageEvent.skill_id == skill.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].source_kind == "chat_execution"
    assert rows[0].conversation_id == conversation_id
    assert rows[0].execution_count == 1


async def test_record_chat_execution_nonfatal_swallows_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _broken_session():
        raise RuntimeError("db down")

    monkeypatch.setattr(skill_usage_service, "async_session", _broken_session)
    # Must not raise — accounting never fails the tool call.
    await skill_usage_service.record_chat_execution_nonfatal(
        skill_id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        thread_id="not-a-uuid",
        agent_id=None,
    )


# ---------------------------------------------------------------------------
# Executor gate — only real chat executions reach the ledger
# ---------------------------------------------------------------------------


def _executable_skill(tmp_path: Path) -> tuple[Path, Path]:
    runtime_root = tmp_path / "runtime"
    skill_dir = runtime_root / "counter"
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "ok.py").write_text("print('ok')\n")
    return runtime_root, skill_dir


async def _run_tool(ctx) -> str:
    from app.agent_runtime.skill_executor import _create_skill_execute_tool

    tool = _create_skill_execute_tool(ctx)
    assert tool.coroutine is not None
    return await tool.coroutine(
        skill_directory=f"/runtime/{ctx.thread_id}/skills/counter/",
        command="python scripts/ok.py",
    )


@pytest.mark.parametrize(
    ("audit_kind", "user_id", "expect_recorded"),
    [
        ("execute_in_skill", TEST_USER_ID, True),
        ("skill_evaluation", TEST_USER_ID, False),
        ("skill_builder.draft_test", TEST_USER_ID, False),
        ("execute_in_skill", None, False),
    ],
)
async def test_executor_records_only_chat_executions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    audit_kind: str,
    user_id,
    expect_recorded: bool,
) -> None:
    from app.marketplace.skill_runtime import SkillRuntimeDescriptor, SkillToolContext

    runtime_root, skill_dir = _executable_skill(tmp_path)
    descriptor = SkillRuntimeDescriptor(
        id=uuid.uuid4(),
        slug="counter",
        name="Counter",
        description="counts executions",
        original_storage_path=skill_dir,
        runtime_storage_path=skill_dir,
    )
    ctx = SkillToolContext(
        thread_id=str(uuid.uuid4()),
        output_dir=tmp_path / "outputs",
        runtime_root=runtime_root,
        descriptors={"counter": descriptor},
        user_id=user_id,
        audit_kind=audit_kind,  # type: ignore[arg-type]
    )

    recorded: list[dict] = []

    async def _fake_record(**kwargs) -> None:
        recorded.append(kwargs)

    monkeypatch.setattr(
        skill_usage_service,
        "record_chat_execution_nonfatal",
        _fake_record,
    )

    result = await _run_tool(ctx)

    assert "ok" in result
    assert bool(recorded) is expect_recorded
    if expect_recorded:
        assert recorded[0]["skill_id"] == descriptor.id
        assert recorded[0]["thread_id"] == ctx.thread_id


async def test_executor_does_not_record_failed_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spec §5.3/§10.3 — only SUCCESSFUL (exit 0) executions count; a crashing
    script must NOT inflate the skill's execution stat (review R5).
    """

    from app.agent_runtime.skill_executor import _create_skill_execute_tool
    from app.marketplace.skill_runtime import SkillRuntimeDescriptor, SkillToolContext

    runtime_root = tmp_path / "runtime"
    skill_dir = runtime_root / "counter"
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "boom.py").write_text("import sys\nsys.exit(1)\n")

    descriptor = SkillRuntimeDescriptor(
        id=uuid.uuid4(),
        slug="counter",
        name="Counter",
        description="fails",
        original_storage_path=skill_dir,
        runtime_storage_path=skill_dir,
    )
    ctx = SkillToolContext(
        thread_id=str(uuid.uuid4()),
        output_dir=tmp_path / "outputs",
        runtime_root=runtime_root,
        descriptors={"counter": descriptor},
        user_id=TEST_USER_ID,
        audit_kind="execute_in_skill",
    )

    recorded: list[dict] = []

    async def _fake_record(**kwargs) -> None:
        recorded.append(kwargs)

    monkeypatch.setattr(skill_usage_service, "record_chat_execution_nonfatal", _fake_record)

    tool = _create_skill_execute_tool(ctx)
    assert tool.coroutine is not None
    await tool.coroutine(
        skill_directory=f"/runtime/{ctx.thread_id}/skills/counter/",
        command="python scripts/boom.py",
    )

    assert recorded == []  # failed script → no usage event


# ---------------------------------------------------------------------------
# API — GET /api/skills/{id}/usage
# ---------------------------------------------------------------------------


async def test_usage_api_returns_summary(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    skill = await _create_skill(db, tmp_path)
    await record_evaluation_usage(
        db,
        skill_id=skill.id,
        user_id=TEST_USER_ID,
        evaluation_run_id=None,
        model_name="gpt-5.4",
        tokens_in=100,
        tokens_out=50,
        cost_usd=Decimal("0.001"),
    )
    await db.commit()

    response = await client.get(f"/api/skills/{skill.id}/usage")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["skill_id"] == str(skill.id)
    assert body["days"] == 30
    assert body["tokens_in"] == 100
    assert body["evaluation_run_count"] == 1
    assert body["chat_execution_count"] == 0
    assert len(body["daily"]) == 1


async def test_usage_api_unknown_skill_404(client: AsyncClient) -> None:
    response = await client.get(f"/api/skills/{uuid.uuid4()}/usage")
    assert response.status_code == 404


async def test_usage_api_other_users_skill_404(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    other = await make_user(db)
    await db.commit()
    skill = await _create_skill(db, tmp_path, user_id=other.id, slug="not-mine")

    response = await client.get(f"/api/skills/{skill.id}/usage")
    # Enumeration-safe: someone else's skill is indistinguishable from absent.
    assert response.status_code == 404
