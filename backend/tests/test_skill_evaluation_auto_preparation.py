from __future__ import annotations

import io
import json
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill
from app.models.skill_evaluation import SkillEvaluationSet
from app.schemas.skill_builder import JsonValue
from app.services.skill_evaluation_auto_preparation import (
    prepare_skill_evaluation_set_best_effort,
)
from app.services.skill_evaluation_set_preparation import SkillEvaluationPreparationStatus
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio


async def test_best_effort_prepare_recovers_from_flush_integrity_error(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    # Given: auto-prepare will hit a real DB constraint failure during flush.
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_package_skill(
            db,
            user_id=TEST_USER_ID,
            zip_bytes=_zip_with(
                {
                    "SKILL.md": _skill_md(),
                    "evals/evals.json": json.dumps(
                        {"evals": [{"input": "Classify.", "expected": "Label."}]}
                    ),
                }
            ),
        )

        # When: best-effort preparation runs inside a savepoint.
        with patch(
            "app.services.skill_evaluation_set_preparation.create_evaluation_set",
            _create_invalid_evaluation_set,
        ):
            result = await prepare_skill_evaluation_set_best_effort(
                db=db,
                skill=skill,
                user_id=TEST_USER_ID,
                source_kind="package_import",
                allow_llm_generation=False,
            )

        await db.commit()

    # Then: the failed savepoint does not poison the outer upload transaction.
    assert result.status is SkillEvaluationPreparationStatus.FAILED
    assert result.reason == "unexpected_IntegrityError"
    stored_skill = await db.scalar(select(Skill).where(Skill.id == skill.id))
    assert stored_skill is not None
    assert await db.scalar(select(SkillEvaluationSet)) is None


async def _create_invalid_evaluation_set(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    skill: Skill,
    name: str,
    evals: list[JsonValue],
    description: str | None = None,
    source_kind: str = "builder",
    template_key: str | None = None,
    template_version: str | None = None,
    generation_strategy: dict[str, JsonValue] | None = None,
) -> SkillEvaluationSet:
    row = SkillEvaluationSet(
        user_id=user_id,
        skill_id=skill.id,
        name=None,
        description=description,
        source_kind=source_kind,
        template_key=template_key,
        template_version=template_version,
        generation_strategy=generation_strategy,
        evals=evals,
    )
    db.add(row)
    await db.flush()
    return row


def _zip_with(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def _skill_md() -> str:
    return (
        "---\n"
        "name: flush-failure\n"
        'description: "Use when testing auto preparation savepoints."\n'
        "---\n\n"
        "Follow the task.\n"
    )
