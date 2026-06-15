from __future__ import annotations

import io
import json
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.skills import service as skill_service

pytestmark = pytest.mark.asyncio


async def test_upload_package_with_claude_evals_creates_evaluation_set(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    # Given: a package upload that contains Claude-style evals/evals.json.
    package = _zip_with(
        {
            "SKILL.md": _skill_md("upload-eval"),
            "evals/evals.json": json.dumps(
                {
                    "skill_name": "upload-eval",
                    "evals": [
                        {
                            "id": "case-001",
                            "prompt": "Extract action items.",
                            "expected_output": "Action item table.",
                        }
                    ],
                }
            ),
        }
    )

    # When: the package is uploaded.
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        response = await client.post(
            "/api/skills/upload",
            files={"file": ("upload.skill", package, "application/zip")},
        )

    # Then: a prepared evaluation set exists and no run is created.
    assert response.status_code == 201, response.text
    skill_id = uuid.UUID(response.json()["id"])
    evaluation_set = await _latest_evaluation_set(db, skill_id)
    assert evaluation_set is not None
    assert evaluation_set.source_kind == "package_import"
    assert evaluation_set.evals[0]["input"] == "Extract action items."
    assert evaluation_set.evals[0]["metadata"]["source_schema"] == "claude_skill_creator"
    audit_event = await _latest_audit_event(db, "skill_evaluation_set.imported")
    assert audit_event is not None
    assert audit_event.target_id == str(skill_id)
    assert audit_event.event_metadata is not None
    assert audit_event.event_metadata["source_kind"] == "package_import"
    assert audit_event.event_metadata["case_count"] == 1
    assert "Extract action items." not in json.dumps(audit_event.event_metadata)
    assert await _run_count(db) == 0


async def test_upload_package_without_evals_succeeds_without_run(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    # Given: a package upload without embedded evals and without system LLM setup.
    package = _zip_with({"SKILL.md": _skill_md("upload-no-eval")})

    # When: the package is uploaded.
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        response = await client.post(
            "/api/skills/upload",
            files={"file": ("upload.skill", package, "application/zip")},
        )

    # Then: upload still succeeds and no run is created.
    assert response.status_code == 201, response.text
    assert await _run_count(db) == 0


async def test_upload_package_succeeds_when_auto_prepare_fails(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    # Given: a package without evals and an LLM preparation failure.
    package = _zip_with({"SKILL.md": _skill_md("upload-prepare-fails")})

    # When: the package is uploaded.
    with (
        patch.object(skill_service.settings, "data_root", str(tmp_path)),
        patch(
            "app.services.skill_evaluation_set_preparation.generate_skill_smoke_eval_payload",
            side_effect=RuntimeError("provider 429"),
        ),
    ):
        response = await client.post(
            "/api/skills/upload",
            files={"file": ("upload.skill", package, "application/zip")},
        )

    # Then: upload succeeds, no run starts, and a failed preparation audit remains.
    assert response.status_code == 201, response.text
    skill_id = uuid.UUID(response.json()["id"])
    assert await _latest_evaluation_set(db, skill_id) is None
    audit_event = await _latest_audit_event(db, "skill_evaluation_set.prepare_failed")
    assert audit_event is not None
    assert audit_event.target_id == str(skill_id)
    assert audit_event.event_metadata is not None
    assert audit_event.event_metadata["status"] == "failed"
    assert audit_event.event_metadata["source_kind"] == "package_import"
    assert await _run_count(db) == 0


def _zip_with(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            archive.writestr(path, data)
    return buffer.getvalue()


def _skill_md(name: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        'description: "Use when testing upload eval preparation."\n'
        'version: "1.0.0"\n'
        "---\n\n"
        "Follow the task.\n"
    )


async def _latest_evaluation_set(
    db: AsyncSession,
    skill_id: uuid.UUID,
) -> SkillEvaluationSet | None:
    result = await db.execute(
        select(SkillEvaluationSet)
        .where(SkillEvaluationSet.skill_id == skill_id)
        .order_by(SkillEvaluationSet.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _run_count(db: AsyncSession) -> int:
    result = await db.scalar(select(func.count()).select_from(SkillEvaluationRun))
    return int(result or 0)


async def _latest_audit_event(db: AsyncSession, action: str) -> AuditEvent | None:
    result = await db.execute(
        select(AuditEvent)
        .where(AuditEvent.action == action)
        .order_by(AuditEvent.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
