from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill
from app.models.skill_builder_session import SkillBuilderSession
from app.models.skill_revision import SkillRevision
from app.services import skill_revision_retention, skill_revision_service
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID


def _skill_content(name: str = "revision-backfill", body: str = "Initial body.") -> str:
    return f'---\nname: {name}\ndescription: "Use when testing revision backfill."\n---\n\n{body}\n'


async def _create_text_skill(db: AsyncSession) -> Skill:
    return await skill_service.create_text_skill(
        db,
        user_id=TEST_USER_ID,
        name="Revision Backfill",
        slug="revision-backfill",
        description="Use when testing revision backfill.",
        content=_skill_content(),
    )


@pytest.mark.asyncio
async def test_backfill_missing_revisions_creates_baseline_and_is_idempotent(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await _create_text_skill(db)

        first_count = await skill_revision_retention.backfill_missing_revisions(db)
        second_count = await skill_revision_retention.backfill_missing_revisions(db)
        result = await db.execute(select(SkillRevision).where(SkillRevision.skill_id == skill.id))
        revisions = list(result.scalars().all())

    assert first_count == 1
    assert second_count == 0
    assert len(revisions) == 1
    assert revisions[0].operation == "create"
    assert revisions[0].metadata_json["backfilled"] is True
    assert revisions[0].metadata_json["baseline"] is True
    assert skill.current_revision_id == revisions[0].id


@pytest.mark.asyncio
async def test_prune_revisions_keeps_latest_current_published_and_active_builder_base(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    old_time = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=365)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await _create_text_skill(db)
        revisions: list[SkillRevision] = []
        for index in range(25):
            await skill_service.update_text_content(
                db,
                skill=skill,
                content=_skill_content(body=f"Body {index}"),
            )
            revision = await skill_revision_service.create_revision_for_skill(
                db,
                skill=skill,
                user_id=TEST_USER_ID,
                operation="manual_content_update" if index else "create",
            )
            revision.created_at = old_time
            revisions.append(revision)

        revisions[1].metadata_json = {"marketplace_published": True}
        db.add(
            SkillBuilderSession(
                user_id=TEST_USER_ID,
                user_request="Improve from old baseline",
                mode="improve",
                source_skill_id=skill.id,
                base_content_hash=revisions[2].content_hash,
                status="review",
            )
        )
        await db.flush()
        pruned = await skill_revision_retention.prune_revisions_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            now=datetime.now(UTC).replace(tzinfo=None),
        )

    pruned_numbers = {revision.revision_number for revision in pruned}
    kept_numbers = {
        revision.revision_number
        for revision in await skill_revision_service.list_revisions(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
        )
        if not revision.metadata_json.get("snapshot_pruned")
    }

    assert pruned_numbers == {1, 4, 5}
    assert {2, 3, *range(6, 26)} <= kept_numbers
    for revision in pruned:
        assert revision.metadata_json["snapshot_pruned"] is True
        assert not (tmp_path / revision.object_key).exists()
