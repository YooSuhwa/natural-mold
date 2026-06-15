from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill
from app.schemas.skill_builder import JsonValue
from app.services.skill_evaluation_set_preparation import prepare_skill_evaluation_set
from app.storage.paths import ensure_relative
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio
_DATA_ROOT_PATCH = "app.storage.paths.settings.data_root"


async def test_prepare_locks_skill_before_duplicate_check(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: an importable evaluation payload and a lock probe.
    skill = await _package_skill(
        db,
        tmp_path,
        eval_payload={"evals": [{"input": "Classify.", "expected": "Label."}]},
    )
    lock_calls: list[uuid.UUID] = []

    async def recording_lock(_db: AsyncSession, *, skill: Skill) -> Skill:
        lock_calls.append(skill.id)
        return skill

    async def assert_lock_seen(
        _db: AsyncSession,
        *,
        skill: Skill,
        user_id: uuid.UUID,
        evals_hash_value: str,
        payload_hash_value: str,
    ) -> bool:
        assert lock_calls == [skill.id]
        assert user_id == TEST_USER_ID
        assert evals_hash_value.startswith("sha256:")
        assert payload_hash_value.startswith("sha256:")
        return False

    monkeypatch.setattr(
        "app.services.skill_evaluation_set_preparation.lock_skill_for_mutation",
        recording_lock,
    )
    monkeypatch.setattr(
        "app.services.skill_evaluation_set_preparation._has_duplicate",
        assert_lock_seen,
    )

    # When: preparation persists the payload.
    with patch(_DATA_ROOT_PATCH, str(tmp_path)):
        await prepare_skill_evaluation_set(
            db=db,
            skill=skill,
            user_id=TEST_USER_ID,
            source_kind="package_import",
            allow_llm_generation=False,
        )

    # Then: the skill row lock was acquired exactly once.
    assert lock_calls == [skill.id]


async def _package_skill(
    db: AsyncSession,
    tmp_path: Path,
    *,
    eval_payload: dict[str, JsonValue],
) -> Skill:
    skill_id = uuid.uuid4()
    root = tmp_path / "skills" / str(skill_id)
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: imported\n"
        'description: "Use when importing evals."\n'
        "---\n\n"
        "Follow the eval instructions.\n",
        encoding="utf-8",
    )
    eval_dir = root / "evals"
    eval_dir.mkdir()
    (eval_dir / "evals.json").write_text(
        json.dumps(eval_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    skill = Skill(
        id=skill_id,
        user_id=TEST_USER_ID,
        name="Imported",
        slug=f"imported-{skill_id.hex[:8]}",
        description="Use when importing evals.",
        kind="package",
        storage_path=ensure_relative(f"skills/{skill_id}"),
        content_hash="hash",
        size_bytes=1,
        version="1.0.0",
        package_metadata={"name": "imported"},
    )
    db.add(skill)
    await db.flush()
    return skill
