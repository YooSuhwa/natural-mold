from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.audit_event import AuditEvent
from app.models.system_llm_setting import SystemLlmSetting
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio

BASE = "/api/skill-builder"


async def _configure_system_llm(db: AsyncSession) -> None:
    credential = await credential_service.create(
        db,
        user_id=None,
        definition_key="openai",
        name="builder-key",
        data={"api_key": "sk-test"},
        is_system=True,
    )
    db.add(
        SystemLlmSetting(
            role="text_primary",
            credential_id=credential.id,
            model_name="gpt-5.4",
        )
    )
    await db.commit()


def _skill_content(name: str = "notes", body: str = "Use when summarizing notes.") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        'description: "Use when summarizing notes into action items."\n'
        "---\n\n"
        f"{body}\n"
    )


def _draft_payload(*, body: str = "Return action items with owners.") -> dict[str, object]:
    return {
        "name": "Notes",
        "slug": "notes",
        "description": "Use when summarizing notes into action items.",
        "files": [
            {"path": "SKILL.md", "content": _skill_content(body=body), "role": "skill"},
            {
                "path": "agents/openai.yaml",
                "content": 'interface:\n  default_prompt: "$notes summarize"\n',
                "role": "metadata",
            },
        ],
        "credential_requirements": [],
        "execution_profile": {"requires_network": False},
    }


async def _audit_events(db: AsyncSession, *actions: str) -> list[AuditEvent]:
    result = await db.execute(select(AuditEvent).where(AuditEvent.action.in_(actions)))
    return list(result.scalars().all())


async def test_confirm_create_writes_sanitized_audit_and_revision_create(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    await _configure_system_llm(db)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        start = await client.post(
            BASE,
            json={
                "mode": "create",
                "user_request": "PROMPT_MARKER_CREATE: 회의록 스킬 만들어줘",
            },
        )
        session_id = start.json()["id"]
        await client.post(
            f"{BASE}/{session_id}/validate",
            json=_draft_payload(body="FILE_BODY_MARKER_CREATE"),
        )

        confirm = await client.post(f"{BASE}/{session_id}/confirm")

    assert confirm.status_code == 201, confirm.text
    events = await _audit_events(db, "skill_builder.confirm_create", "skill_revision.create")
    actions = {event.action for event in events}
    assert actions == {"skill_builder.confirm_create", "skill_revision.create"}

    metadata_text = str([event.event_metadata for event in events])
    assert "PROMPT_MARKER_CREATE" not in metadata_text
    assert "FILE_BODY_MARKER_CREATE" not in metadata_text

    confirm_event = next(
        event for event in events if event.action == "skill_builder.confirm_create"
    )
    assert confirm_event.event_metadata is not None
    assert confirm_event.event_metadata["session_id"] == session_id
    assert confirm_event.event_metadata["file_count"] == 2
    assert confirm_event.event_metadata["credential_requirement_count"] == 0
    assert confirm_event.event_metadata["new_hash"] == confirm.json()["content_hash"]

    revision_event = next(event for event in events if event.action == "skill_revision.create")
    assert revision_event.target_type == "skill_revision"
    assert revision_event.event_metadata is not None
    assert revision_event.event_metadata["skill_id"] == confirm.json()["id"]
    assert revision_event.event_metadata["revision_number"] == 1
    assert revision_event.event_metadata["operation"] == "builder_create"
    assert revision_event.event_metadata["content_hash"] == confirm.json()["content_hash"]
    assert revision_event.event_metadata["file_count"] == 2


async def test_improve_confirm_audit_includes_counts_and_hashes_without_content(
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
            content=_skill_content(body="BASE_FILE_BODY_MARKER"),
        )
        await db.commit()
        old_hash = skill.content_hash

        start = await client.post(
            BASE,
            json={
                "mode": "improve",
                "source_skill_id": str(skill.id),
                "user_request": "PROMPT_MARKER_IMPROVE: 개선해줘",
            },
        )
        session_id = start.json()["id"]
        await client.post(
            f"{BASE}/{session_id}/validate",
            json=_draft_payload(body="NEW_FILE_BODY_MARKER"),
        )

        confirm = await client.post(f"{BASE}/{session_id}/confirm")

    assert confirm.status_code == 201, confirm.text
    events = await _audit_events(db, "skill_builder.apply_improvement", "skill_revision.create")
    metadata_text = str([event.event_metadata for event in events])
    assert "PROMPT_MARKER_IMPROVE" not in metadata_text
    assert "BASE_FILE_BODY_MARKER" not in metadata_text
    assert "NEW_FILE_BODY_MARKER" not in metadata_text

    apply_event = next(
        event for event in events if event.action == "skill_builder.apply_improvement"
    )
    assert apply_event.event_metadata is not None
    assert apply_event.event_metadata["session_id"] == session_id
    assert apply_event.event_metadata["source_skill_id"] == str(skill.id)
    assert apply_event.event_metadata["old_hash"] == old_hash
    assert apply_event.event_metadata["new_hash"] == confirm.json()["content_hash"]
    assert apply_event.event_metadata["file_count"] == 2
    assert apply_event.event_metadata["changed_file_count"] == 1
    assert apply_event.event_metadata["added_file_count"] == 1
    assert apply_event.event_metadata["deleted_file_count"] == 0

    revision_event = next(event for event in events if event.action == "skill_revision.create")
    assert revision_event.event_metadata is not None
    assert revision_event.event_metadata["operation"] == "builder_improvement"
    assert revision_event.event_metadata["content_hash"] == confirm.json()["content_hash"]
