from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.audit_event import AuditEvent
from app.models.skill_builder_session import SkillBuilderSession
from app.services import skill_builder_service, skill_revision_service
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID
from tests.skill_builder_test_helpers import configure_system_llm as _configure_system_llm

pytestmark = pytest.mark.asyncio

BASE = "/api/skill-builder"


@pytest.fixture(autouse=True)
def _tmp_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """start v2가 드래프트 워크스페이스를 디스크에 만들므로 data_root 격리."""

    monkeypatch.setattr(settings, "data_root", str(tmp_path))


def _skill_content(name: str = "notes", body: str = "Use when summarizing notes.") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        'description: "Use when summarizing notes into action items."\n'
        "---\n\n"
        f"{body}\n"
    )


def _draft_payload(body: str = "Return action items.") -> dict[str, object]:
    return {
        "name": "Notes",
        "slug": "notes",
        "description": "Use when summarizing notes into action items.",
        "files": [{"path": "SKILL.md", "content": _skill_content(body=body), "role": "skill"}],
        "credential_requirements": [],
        "execution_profile": {"requires_network": False},
    }


async def _audit_events(db: AsyncSession, action: str) -> list[AuditEvent]:
    result = await db.execute(select(AuditEvent).where(AuditEvent.action == action))
    return list(result.scalars().all())


async def test_get_builder_session_is_user_scoped(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _configure_system_llm(db)
    owned = await client.post(BASE, json={"mode": "create", "user_request": "owned"})
    unowned = await skill_builder_service.create_session(
        db,
        user_id=uuid.uuid4(),
        user_request="other user session",
    )
    await db.commit()

    owned_get = await client.get(f"{BASE}/{owned.json()['id']}")
    unowned_get = await client.get(f"{BASE}/{unowned.id}")

    assert owned_get.status_code == 200, owned_get.text
    assert unowned_get.status_code == 404


async def test_system_llm_missing_and_session_create_audits_are_sanitized(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    missing = await client.post(
        BASE,
        json={"mode": "create", "user_request": "PROMPT_MARKER_MISSING"},
    )
    session_count = await db.scalar(select(func.count()).select_from(SkillBuilderSession))
    missing_events = await _audit_events(db, "skill_builder.system_model_missing")

    await _configure_system_llm(db)
    created = await client.post(
        BASE,
        json={"mode": "create", "user_request": "PROMPT_MARKER_CREATE"},
    )
    create_events = await _audit_events(db, "skill_builder.session_create")

    assert missing.status_code == 409
    assert session_count == 0
    assert missing_events[0].outcome == "denied"
    assert "PROMPT_MARKER_MISSING" not in str(missing_events[0].event_metadata)
    assert created.status_code == 201, created.text
    assert create_events[0].event_metadata is not None
    assert create_events[0].event_metadata["session_id"] == created.json()["id"]
    assert "PROMPT_MARKER_CREATE" not in str(create_events[0].event_metadata)


async def test_improve_conflict_audit_excludes_prompt_and_file_content(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    await _configure_system_llm(db)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Notes",
            slug="notes",
            description="Use when summarizing notes.",
            content=_skill_content(body="BASE_MARKER"),
        )
        await db.commit()
        started = await client.post(
            BASE,
            json={
                "mode": "improve",
                "source_skill_id": str(skill.id),
                "user_request": "PROMPT_MARKER_CONFLICT",
            },
        )
        await skill_service.update_text_content(
            db,
            skill=skill,
            content=_skill_content(body="MANUAL_MARKER"),
        )
        await db.commit()
        await client.post(
            f"{BASE}/{started.json()['id']}/validate",
            json=_draft_payload(body="DRAFT_MARKER"),
        )
        response = await client.post(f"{BASE}/{started.json()['id']}/confirm")

    events = await _audit_events(db, "skill_builder.apply_conflict")
    metadata_text = str([event.event_metadata for event in events])
    assert response.status_code == 409
    assert "PROMPT_MARKER_CONFLICT" not in metadata_text
    assert "BASE_MARKER" not in metadata_text
    assert "MANUAL_MARKER" not in metadata_text
    assert "DRAFT_MARKER" not in metadata_text
    assert events[0].outcome == "denied"


async def test_rollback_audit_is_sanitized_and_unowned_rollback_returns_404(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Notes",
            slug="notes",
            description="Use when summarizing notes.",
            content=_skill_content(body="ROLLBACK_ORIGINAL_MARKER"),
        )
        original = await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="create",
        )
        await skill_service.update_text_content(
            db,
            skill=skill,
            content=_skill_content(body="ROLLBACK_CHANGED_MARKER"),
        )
        await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="manual_content_update",
        )
        other_skill = await skill_service.create_text_skill(
            db,
            user_id=uuid.uuid4(),
            name="Other",
            slug="other",
            description="Use when summarizing notes.",
            content=_skill_content("other"),
        )
        other_revision = await skill_revision_service.create_revision_for_skill(
            db,
            skill=other_skill,
            user_id=other_skill.user_id,
            operation="create",
        )
        await db.commit()

        rollback = await client.post(f"/api/skills/{skill.id}/revisions/{original.id}/rollback")
        unowned = await client.post(
            f"/api/skills/{other_skill.id}/revisions/{other_revision.id}/rollback"
        )

    events = await _audit_events(db, "skill_revision.rollback")
    metadata_text = str([event.event_metadata for event in events])
    assert rollback.status_code == 200, rollback.text
    assert unowned.status_code == 404
    assert events[0].event_metadata is not None
    assert events[0].event_metadata["restored_revision_id"] == str(original.id)
    assert "ROLLBACK_ORIGINAL_MARKER" not in metadata_text
    assert "ROLLBACK_CHANGED_MARKER" not in metadata_text
