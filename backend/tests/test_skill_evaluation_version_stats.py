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

    # Fixture designed so the expected output order matches NEITHER insertion
    # order NOR version-string order — only the intended sort(last_run_at asc).
    # Three groups; the two 1.0.0 groups differ ONLY by content_hash, proving
    # grouping is by (version, hash), not version alone.
    #
    #   group A: 1.0.0 / hash-v1  → last run at base+3h  (LATEST)
    #   group B: 1.0.0 / hash-alt → last run at base+1h
    #   group C: 1.1.0 / hash-v2  → last run at base     (EARLIEST)
    #
    # Insert in the reverse of the expected output ([A, B, C]) so insertion
    # order is a decoy.
    db.add(
        _run(  # A: two completed runs (0.5 → 0.7) + one failed (ignored)
            skill_id=skill.id,
            set_id=eval_set.id,
            version="1.0.0",
            content_hash="hash-v1",
            pass_rate=0.5,
            created_at=base + timedelta(hours=2),
        )
    )
    db.add(
        _run(
            skill_id=skill.id,
            set_id=eval_set.id,
            version="1.0.0",
            content_hash="hash-v1",
            pass_rate=0.7,
            created_at=base + timedelta(hours=3),
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
            created_at=base + timedelta(hours=4),
            status="failed",
        )
    )
    db.add(
        _run(  # B: same version 1.0.0 but a DIFFERENT content hash
            skill_id=skill.id,
            set_id=eval_set.id,
            version="1.0.0",
            content_hash="hash-alt",
            pass_rate=0.9,
            created_at=base + timedelta(hours=1),
        )
    )
    db.add(
        _run(  # C: 1.1.0, earliest last run → must sort FIRST
            skill_id=skill.id,
            set_id=eval_set.id,
            version="1.1.0",
            content_hash="hash-v2",
            pass_rate=1.0,
            created_at=base,
            benchmark={"pass_rate_delta": 0.6, "measured": True},
        )
    )
    await db.commit()

    response = await client.get(f"/api/skills/{skill.id}/evaluations/version-stats")

    assert response.status_code == 200, response.text
    body = response.json()
    # Ordered by each group's last_run_at ascending (C, B, A) — NOT version
    # string order (which would put both 1.0.0 groups before 1.1.0).
    assert [(item["skill_version"], item["content_hash"]) for item in body] == [
        ("1.1.0", "hash-v2"),
        ("1.0.0", "hash-alt"),
        ("1.0.0", "hash-v1"),
    ]

    a = body[2]  # 1.0.0 / hash-v1
    assert a["run_count"] == 2  # failed run excluded
    assert a["latest_pass_rate"] == 0.7
    assert a["avg_pass_rate"] == pytest.approx(0.6)
    assert a["latest_pass_rate_delta"] == pytest.approx(0.4)
    assert a["latest_measured"] is True

    b = body[1]  # 1.0.0 / hash-alt — separate group despite same version
    assert b["run_count"] == 1
    assert b["latest_pass_rate"] == 0.9

    c = body[0]  # 1.1.0
    assert c["run_count"] == 1
    assert c["latest_pass_rate"] == 1.0


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
