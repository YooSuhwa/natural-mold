from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.models.marketplace import MarketplaceItem, MarketplaceVersion
from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.models.user import User
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio


async def test_marketplace_install_with_embedded_evals_creates_evaluation_set(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    # Given: a published marketplace skill version with embedded evals.
    await _ensure_test_user(db)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        item = await _published_skill_item(db, tmp_path)
        await db.commit()

        # When: the user installs the marketplace skill.
        response = await client.post(
            f"/api/marketplace/items/{item.id}/install",
            json={"install_mode": "reuse_or_update"},
        )

    # Then: an eval set is prepared and no eval run is created.
    assert response.status_code == 201, response.text
    installed_skill_id = uuid.UUID(response.json()["installed_skill_id"])
    evaluation_set = await _latest_evaluation_set(db, installed_skill_id)
    assert evaluation_set is not None
    assert evaluation_set.source_kind == "marketplace_import"
    assert evaluation_set.evals[0]["input"] == "Extract marketplace action items."
    audit_event = await _latest_audit_event(db, "skill_evaluation_set.imported")
    assert audit_event is not None
    assert audit_event.target_id == str(installed_skill_id)
    assert audit_event.event_metadata is not None
    assert audit_event.event_metadata["source_kind"] == "marketplace_import"
    assert audit_event.event_metadata["case_count"] == 1
    assert "Extract marketplace action items." not in json.dumps(audit_event.event_metadata)
    assert await _run_count(db) == 0


async def test_marketplace_install_without_evals_succeeds_without_run(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    # Given: a marketplace skill version without evals and without system LLM setup.
    await _ensure_test_user(db)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        item = await _published_skill_item(db, tmp_path, include_evals=False)
        await db.commit()

        # When: the user installs the marketplace skill.
        response = await client.post(
            f"/api/marketplace/items/{item.id}/install",
            json={"install_mode": "reuse_or_update"},
        )

    # Then: install succeeds and no evaluation run is created.
    assert response.status_code == 201, response.text
    assert await _run_count(db) == 0


async def test_marketplace_install_succeeds_when_auto_prepare_fails(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    # Given: a marketplace skill without evals and an LLM preparation failure.
    await _ensure_test_user(db)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        item = await _published_skill_item(db, tmp_path, include_evals=False)
        await db.commit()

        # When: the user installs the marketplace skill.
        with patch(
            "app.services.skill_evaluation_set_preparation.generate_skill_smoke_eval_payload",
            side_effect=RuntimeError("provider timeout"),
        ):
            response = await client.post(
                f"/api/marketplace/items/{item.id}/install",
                json={"install_mode": "reuse_or_update"},
            )

    # Then: install succeeds and the failed preparation is audited.
    assert response.status_code == 201, response.text
    installed_skill_id = uuid.UUID(response.json()["installed_skill_id"])
    assert await _latest_evaluation_set(db, installed_skill_id) is None
    audit_event = await _latest_audit_event(db, "skill_evaluation_set.prepare_failed")
    assert audit_event is not None
    assert audit_event.target_id == str(installed_skill_id)
    assert audit_event.event_metadata is not None
    assert audit_event.event_metadata["status"] == "failed"
    assert audit_event.event_metadata["source_kind"] == "marketplace_import"
    assert await _run_count(db) == 0


async def _ensure_test_user(db: AsyncSession) -> None:
    user = await db.get(User, TEST_USER_ID)
    if user is None:
        db.add(
            User(
                id=TEST_USER_ID,
                email="test@test.com",
                name="Test",
                hashed_password="h",
                is_active=True,
                is_super_user=True,
            )
        )
        await db.flush()


async def _published_skill_item(
    db: AsyncSession,
    tmp_path: Path,
    *,
    include_evals: bool = True,
) -> MarketplaceItem:
    version_id = uuid.uuid4()
    version_dir = tmp_path / "marketplace-versions" / str(version_id)
    version_dir.mkdir(parents=True)
    (version_dir / "SKILL.md").write_text(
        "---\nname: marketplace-eval\n"
        'description: "Use when testing marketplace eval preparation."\n'
        'version: "1.0.0"\n'
        "---\n\n"
        "Follow the task.\n",
        encoding="utf-8",
    )
    if include_evals:
        eval_dir = version_dir / "evals"
        eval_dir.mkdir()
        (eval_dir / "evals.json").write_text(
            json.dumps(
                {
                    "skill_name": "marketplace-eval",
                    "evals": [
                        {
                            "prompt": "Extract marketplace action items.",
                            "expected_output": "Action item table.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
    item = MarketplaceItem(
        id=uuid.uuid4(),
        resource_type="skill",
        owner_user_id=None,
        is_system=False,
        is_listed=True,
        name="Marketplace Eval",
        slug=f"marketplace-eval-{uuid.uuid4().hex[:8]}",
        description="Use when testing marketplace eval preparation.",
        visibility="public",
        status="published",
        moderation_status="approved",
        source_kind="user",
        published_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(item)
    await db.flush()
    version = MarketplaceVersion(
        id=version_id,
        item_id=item.id,
        version_label="1.0.0",
        version_number=1,
        resource_type="skill",
        payload_kind="skill_package",
        payload={"kind": "package", "name": "marketplace-eval", "version": "1.0.0"},
        storage_path=str(version_dir),
        content_hash="deadbeef" * 8,
        size_bytes=512,
        credential_requirements=[],
        execution_profile={"support_level": "stable"},
    )
    db.add(version)
    await db.flush()
    item.latest_version_id = version.id
    await db.flush()
    return item


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
