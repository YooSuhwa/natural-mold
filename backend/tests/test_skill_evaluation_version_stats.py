"""Phase 3 §6 — 버전별 통과율 집계 API."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _create_skill(db: AsyncSession, tmp_path: Path):
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Version Stats",
            slug=f"version-stats-{uuid.uuid4().hex[:8]}",
            description="Use when testing version stats.",
            content=(
                "---\nname: version-stats\n"
                'description: "Use when testing version stats."\n---\n\nBody.\n'
            ),
            version="1.0.0",
        )
        await db.commit()
        return skill


def _run(
    *,
    skill_id: uuid.UUID,
    set_id: uuid.UUID,
    version: str,
    content_hash: str,
    pass_rate: float | None,
    created_at: datetime,
    status: str = "completed",
    benchmark: dict | None = None,
    user_id: uuid.UUID = TEST_USER_ID,
) -> SkillEvaluationRun:
    return SkillEvaluationRun(
        user_id=user_id,
        skill_id=skill_id,
        evaluation_set_id=set_id,
        status=status,
        skill_version=version,
        skill_content_hash=content_hash,
        summary={"pass_rate": pass_rate} if pass_rate is not None else {},
        benchmark=benchmark,
        created_at=created_at,
    )


async def _seed_set(db: AsyncSession, skill_id: uuid.UUID) -> SkillEvaluationSet:
    row = SkillEvaluationSet(
        user_id=TEST_USER_ID,
        skill_id=skill_id,
        name="stats",
        evals=[{"input": "x"}],
    )
    db.add(row)
    await db.flush()
    return row


async def test_version_stats_groups_and_orders(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    skill = await _create_skill(db, tmp_path)
    eval_set = await _seed_set(db, skill.id)
    base = _now() - timedelta(days=3)

    # v1: two completed runs (0.5 → 0.7), one failed run (ignored).
    db.add(
        _run(
            skill_id=skill.id,
            set_id=eval_set.id,
            version="1.0.0",
            content_hash="hash-v1",
            pass_rate=0.5,
            created_at=base,
        )
    )
    db.add(
        _run(
            skill_id=skill.id,
            set_id=eval_set.id,
            version="1.0.0",
            content_hash="hash-v1",
            pass_rate=0.7,
            created_at=base + timedelta(hours=1),
            benchmark={"pass_rate_delta": 0.4, "measured": True},
        )
    )
    db.add(
        _run(
            skill_id=skill.id,
            set_id=eval_set.id,
            version="1.0.0",
            content_hash="hash-v1",
            pass_rate=None,
            created_at=base + timedelta(hours=2),
            status="failed",
        )
    )
    # v2: one completed run, later — should come last (chronological).
    db.add(
        _run(
            skill_id=skill.id,
            set_id=eval_set.id,
            version="1.1.0",
            content_hash="hash-v2",
            pass_rate=1.0,
            created_at=base + timedelta(days=1),
            benchmark={"pass_rate_delta": 0.6, "measured": True},
        )
    )
    await db.commit()

    response = await client.get(f"/api/skills/{skill.id}/evaluations/version-stats")

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["skill_version"] for item in body] == ["1.0.0", "1.1.0"]

    v1 = body[0]
    assert v1["content_hash"] == "hash-v1"
    assert v1["run_count"] == 2  # failed run excluded
    assert v1["latest_pass_rate"] == 0.7
    assert v1["avg_pass_rate"] == pytest.approx(0.6)
    assert v1["latest_pass_rate_delta"] == pytest.approx(0.4)
    assert v1["latest_measured"] is True

    v2 = body[1]
    assert v2["run_count"] == 1
    assert v2["latest_pass_rate"] == 1.0


async def test_version_stats_empty_and_unknown_skill(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    skill = await _create_skill(db, tmp_path)

    empty = await client.get(f"/api/skills/{skill.id}/evaluations/version-stats")
    assert empty.status_code == 200
    assert empty.json() == []

    unknown = await client.get(f"/api/skills/{uuid.uuid4()}/evaluations/version-stats")
    assert unknown.status_code == 404


async def test_version_stats_ignores_legacy_unmeasured_benchmark(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    skill = await _create_skill(db, tmp_path)
    eval_set = await _seed_set(db, skill.id)
    db.add(
        _run(
            skill_id=skill.id,
            set_id=eval_set.id,
            version="0.9.0",
            content_hash="hash-legacy",
            pass_rate=0.8,
            created_at=_now(),
            benchmark={"pass_rate_delta": 0.8},  # legacy estimate — no measured flag
        )
    )
    await db.commit()

    response = await client.get(f"/api/skills/{skill.id}/evaluations/version-stats")
    body = response.json()
    assert body[0]["latest_measured"] is False
    assert body[0]["latest_pass_rate_delta"] == pytest.approx(0.8)
