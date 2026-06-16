from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.marketplace.install_locks import lock_marketplace_item_install
from app.models.marketplace import MarketplaceInstallation
from app.models.skill import Skill
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID
from tests.test_marketplace_install import _ensure_test_user, _make_published_skill_item


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_lock_marketplace_item_install_uses_row_lock_on_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item_id = uuid.uuid4()
    db = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr("app.marketplace.install_locks.is_postgres", lambda _db: True)

    await lock_marketplace_item_install(db, item_id=item_id)

    db.execute.assert_awaited_once()
    statement = db.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql


@pytest.mark.asyncio
async def test_install_rechecks_existing_installation_after_item_lock(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _ensure_test_user(db)

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        version_dir = tmp_path / "v1"
        item, version = await _make_published_skill_item(
            db,
            storage_path=version_dir,
            requirements=None,
        )
        await db.commit()
        existing_skill_id = uuid.uuid4()
        existing_installation_id = uuid.uuid4()

        async def fake_item_lock(
            lock_db: AsyncSession,
            *,
            item_id: uuid.UUID,
        ) -> None:
            skill = Skill(
                id=existing_skill_id,
                user_id=TEST_USER_ID,
                name="SRT Booker",
                slug="srt-booker",
                description="SRT auto-book",
                kind="package",
                storage_path=f"skills/{existing_skill_id}",
                content_hash=version.content_hash,
                size_bytes=int(version.size_bytes or 0),
                version="0.1.0",
                package_metadata=version.payload,
                used_by_count=0,
                is_system=False,
                source_kind=item.source_kind,
                source_marketplace_item_id=item.id,
                source_marketplace_version_id=version.id,
                credential_requirements=version.credential_requirements,
                execution_profile=version.execution_profile,
                origin_kind="community",
                origin_marketplace_item_id=item.id,
                origin_marketplace_version_id=version.id,
                is_dirty=False,
                last_modified_at=_now(),
            )
            installation = MarketplaceInstallation(
                id=existing_installation_id,
                user_id=TEST_USER_ID,
                item_id=item_id,
                version_id=version.id,
                resource_type="skill",
                installed_skill_id=existing_skill_id,
                install_status="active",
                is_dirty=False,
                installed_at=_now(),
            )
            lock_db.add_all([skill, installation])
            await lock_db.flush()

        monkeypatch.setattr(
            "app.marketplace.install_service.lock_marketplace_item_install",
            fake_item_lock,
        )

        response = await client.post(
            f"/api/marketplace/items/{item.id}/install",
            json={"install_mode": "reuse_or_update"},
        )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"] == str(existing_installation_id)
    assert body["installed_skill_id"] == str(existing_skill_id)
