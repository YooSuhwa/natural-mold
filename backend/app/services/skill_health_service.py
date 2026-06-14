from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from app.models.skill import Skill
from app.models.skill_evaluation import SkillEvaluationRun

SkillHealthState = Literal[
    "ready",
    "needs_evaluation",
    "needs_rerun",
    "needs_credentials",
    "evaluation_running",
    "evaluation_failed",
    "low_confidence",
]

RUNNING_STATUSES = frozenset({"queued", "running", "grading"})
FAILED_STATUSES = frozenset({"failed", "cancelled"})


def calculate_skill_health(
    skill: Skill,
    *,
    latest_run: SkillEvaluationRun | None,
    missing_required_keys: Sequence[str] = (),
) -> dict[str, Any]:
    if missing_required_keys:
        return _health(
            "needs_credentials",
            "자격증명 필요",
            f"Missing required credential bindings: {', '.join(missing_required_keys)}.",
            "warning",
        )
    if latest_run is None:
        return _health("needs_evaluation", "평가 없음", "No evaluation run exists.", "neutral")
    if latest_run.status in RUNNING_STATUSES:
        return _health("evaluation_running", "평가 중", "An evaluation run is in progress.", "info")
    if latest_run.status in FAILED_STATUSES:
        return _health(
            "evaluation_failed",
            "평가 실패",
            "The latest evaluation did not complete.",
            "error",
        )
    if latest_run.skill_content_hash != skill.content_hash:
        return _health(
            "needs_rerun",
            "재평가 필요",
            "Skill content changed after the latest completed evaluation.",
            "warning",
        )
    pass_rate = _pass_rate(latest_run)
    if pass_rate < 0.8:
        return _health(
            "low_confidence",
            "낮은 통과율",
            f"Latest evaluation pass rate is {pass_rate:.0%}.",
            "warning",
        )
    return _health("ready", "검증됨", "Latest evaluation passed for the current skill.", "success")


def _pass_rate(run: SkillEvaluationRun) -> float:
    summary = run.summary or {}
    value = summary.get("pass_rate")
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _health(
    state: SkillHealthState,
    label: str,
    reason: str,
    severity: str,
) -> dict[str, Any]:
    return {"state": state, "label": label, "reason": reason, "severity": severity}
