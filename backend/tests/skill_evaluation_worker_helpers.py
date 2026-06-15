from __future__ import annotations

import asyncio
import io
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.audit_event import AuditEvent
from app.models.marketplace import SkillCredentialBinding
from app.models.skill_evaluation import SkillEvaluationRun
from app.models.user import User
from app.schemas.skill_builder import JsonValue
from app.services import skill_evaluation_service
from app.services.skill_evaluation_worker_types import (
    SkillEvaluationContext,
    SkillEvaluationExecutionError,
    SkillEvaluationResult,
)
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID, TestSession


class FailingEvaluator:
    async def evaluate(
        self,
        _db: AsyncSession,
        context: SkillEvaluationContext,
    ) -> SkillEvaluationResult:
        raise SkillEvaluationExecutionError(f"runner unavailable for {context.run_id}")


class BlockingEvaluator:
    def __init__(self) -> None:
        self.started: asyncio.Queue[uuid.UUID] = asyncio.Queue()
        self.releases: dict[uuid.UUID, asyncio.Event] = {}

    async def evaluate(
        self,
        _db: AsyncSession,
        context: SkillEvaluationContext,
    ) -> SkillEvaluationResult:
        release = asyncio.Event()
        self.releases[context.run_id] = release
        await self.started.put(context.run_id)
        await release.wait()
        return SkillEvaluationResult(
            summary={"case_count": len(context.evals), "pass_rate": 1},
            benchmark={"with_skill_pass_rate": 1, "without_skill_pass_rate": 0},
            case_results=[],
        )


class HangingEvaluator:
    async def evaluate(
        self,
        _db: AsyncSession,
        context: SkillEvaluationContext,
    ) -> SkillEvaluationResult:
        await asyncio.sleep(10)
        return SkillEvaluationResult(
            summary={"case_count": len(context.evals), "pass_rate": 1},
            benchmark={"with_skill_pass_rate": 1, "without_skill_pass_rate": 0},
            case_results=[],
        )


async def create_run(
    db: AsyncSession,
    tmp_path: Path,
    *,
    evals: list[JsonValue] | None = None,
) -> SkillEvaluationRun:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Evaluator",
            slug=f"evaluator-{uuid.uuid4().hex[:8]}",
            description="Use when testing skill evaluation behavior.",
            content=skill_content(),
            version="1.0.0",
        )
    evaluation_set = await skill_evaluation_service.create_evaluation_set(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        name="Smoke",
        evals=evals or [{"input": "a"}],
    )
    return await skill_evaluation_service.create_run(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        evaluation_set=evaluation_set,
    )


async def create_script_run(
    db: AsyncSession,
    tmp_path: Path,
    *,
    script: str,
    command: str,
    credential_secret: str | None = None,
) -> SkillEvaluationRun:
    db.add(
        User(
            id=TEST_USER_ID,
            email="skill-eval-worker@test.com",
            name="Skill Eval Worker",
            hashed_password="h",
            is_active=True,
            is_super_user=False,
        )
    )
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_package_skill(
            db,
            user_id=TEST_USER_ID,
            zip_bytes=package_zip({"SKILL.md": skill_content(), "scripts/probe.py": script}),
            name_override="Evaluator Package",
        )
    if credential_secret is not None:
        skill.credential_requirements = [
            {
                "key": "openai",
                "definition_key": "openai",
                "required": True,
                "label": "OpenAI",
                "fields": ["api_key"],
                "env_map": {"api_key": "OPENAI_API_KEY"},
            }
        ]
        credential = await credential_service.create(
            db,
            user_id=TEST_USER_ID,
            definition_key="openai",
            name="eval key",
            data={"api_key": credential_secret},
        )
        db.add(
            SkillCredentialBinding(
                skill_id=skill.id,
                user_id=TEST_USER_ID,
                requirement_key="openai",
                credential_id=credential.id,
                scope="skill",
            )
        )
    evaluation_set = await skill_evaluation_service.create_evaluation_set(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        name="Script smoke",
        evals=[
            {
                "input": "run the script-backed evaluation case",
                "expected": "script completes",
                "metadata": {"execute_in_skill": {"command": command}},
            }
        ],
    )
    return await skill_evaluation_service.create_run(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        evaluation_set=evaluation_set,
    )


async def audit_actions(db: AsyncSession) -> list[str]:
    result = await db.execute(select(AuditEvent).order_by(AuditEvent.created_at))
    return [row.action for row in result.scalars().all()]


async def wait_for_run_status(run_id: uuid.UUID, status: str) -> SkillEvaluationRun:
    last_seen: str | None = None
    last_error: str | None = None
    for _ in range(40):
        async with TestSession() as session:
            row = await session.get(SkillEvaluationRun, run_id)
            if row is not None:
                last_seen = row.status
                last_error = row.error_message
            if row is not None and row.status == status:
                return row
        await asyncio.sleep(0.05)
    raise AssertionError(f"run did not reach status {status}; last={last_seen}; error={last_error}")


def skill_content() -> str:
    return (
        "---\n"
        "name: evaluator\n"
        'description: "Use when testing skill evaluation behavior."\n'
        "---\n\n"
        "Use when testing evaluation behavior.\n"
    )


def package_zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()
