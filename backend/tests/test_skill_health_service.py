from __future__ import annotations

import uuid

from app.models.skill import Skill
from app.models.skill_evaluation import SkillEvaluationRun
from app.services.skill_health_service import calculate_skill_health


def _skill(content_hash: str = "a" * 64) -> Skill:
    return Skill(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Demo",
        slug="demo",
        description="Use when testing health.",
        kind="text",
        content_hash=content_hash,
        size_bytes=1,
    )


def _run(
    *,
    status: str = "completed",
    content_hash: str = "a" * 64,
    pass_rate: float | None = 0.9,
) -> SkillEvaluationRun:
    summary = None if pass_rate is None else {"pass_rate": pass_rate}
    return SkillEvaluationRun(
        user_id=uuid.uuid4(),
        skill_id=uuid.uuid4(),
        evaluation_set_id=uuid.uuid4(),
        status=status,
        skill_content_hash=content_hash,
        summary=summary,
    )


def test_health_needs_credentials_first() -> None:
    health = calculate_skill_health(
        _skill(),
        latest_run=_run(status="completed", pass_rate=0.95),
        missing_required_keys=["openai"],
    )

    assert health["state"] == "needs_credentials"


def test_health_needs_evaluation_without_run() -> None:
    health = calculate_skill_health(_skill(), latest_run=None)

    assert health["state"] == "needs_evaluation"


def test_health_detects_running_evaluation() -> None:
    health = calculate_skill_health(_skill(), latest_run=_run(status="queued", pass_rate=None))

    assert health["state"] == "evaluation_running"


def test_health_detects_failed_evaluation() -> None:
    health = calculate_skill_health(_skill(), latest_run=_run(status="failed", pass_rate=None))

    assert health["state"] == "evaluation_failed"


def test_health_detects_stale_hash() -> None:
    health = calculate_skill_health(_skill("b" * 64), latest_run=_run(content_hash="a" * 64))

    assert health["state"] == "needs_rerun"


def test_health_detects_low_confidence() -> None:
    health = calculate_skill_health(_skill(), latest_run=_run(pass_rate=0.7))

    assert health["state"] == "low_confidence"


def test_health_treats_boolean_pass_rate_as_missing() -> None:
    run = _run(pass_rate=None)
    run.summary = {"pass_rate": True}

    health = calculate_skill_health(_skill(), latest_run=run)

    assert health["state"] == "low_confidence"


def test_health_ready_when_current_passing_run_exists() -> None:
    health = calculate_skill_health(_skill(), latest_run=_run(pass_rate=0.8))

    assert health["state"] == "ready"
    assert health["severity"] == "success"
