from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.schemas.skill_builder import SkillBuilderMode, SkillBuilderStartRequest
from app.schemas.skill_evaluation import SkillEvaluationRunEstimate
from app.schemas.skill_revision import SkillRevisionOperation, SkillRevisionSummary


def test_skill_builder_start_request_accepts_create_mode_without_source_skill() -> None:
    payload = SkillBuilderStartRequest(
        mode=SkillBuilderMode.CREATE,
        user_request="회의록 액션 아이템 스킬을 만들어줘.",
    )

    assert payload.source_skill_id is None


def test_skill_builder_start_request_rejects_improve_mode_without_source_skill() -> None:
    with pytest.raises(ValidationError):
        SkillBuilderStartRequest(
            mode=SkillBuilderMode.IMPROVE,
            user_request="기존 스킬을 개선해줘.",
        )


def test_evaluation_estimate_exposes_cost_and_time_guard_fields() -> None:
    estimate = SkillEvaluationRunEstimate(
        case_count=3,
        model_call_count=9,
        estimated_seconds=45,
        timeout_seconds=180,
        estimated_cost_usd=0.08,
        uses_baseline_comparison=True,
    )

    assert estimate.case_count == 3
    assert estimate.uses_baseline_comparison is True


def test_revision_summary_uses_typed_operation_values() -> None:
    summary = SkillRevisionSummary(
        id=uuid.uuid4(),
        skill_id=uuid.uuid4(),
        revision_number=1,
        operation=SkillRevisionOperation.CREATE,
        content_hash="a" * 64,
        created_at="2026-06-15T00:00:00Z",
    )

    assert summary.operation is SkillRevisionOperation.CREATE
