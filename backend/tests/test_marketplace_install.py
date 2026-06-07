"""ADR-017 Slice B — marketplace install flow integration smoke.

Covers the happy paths Sat called out in the M4 brief:

* fresh install → user-owned Skill row + Installation row + filesystem copy
* missing required binding → ``install_status='needs_setup'``
* ``reuse_or_update`` returns the prior installation
* DELETE soft-uninstalls; ``?delete_resource=true`` cascades

Test layout mirrors existing patterns (``tests/test_skills.py`` for
on-disk verification; ``tests/test_skill_bindings.py`` for the fixture
helpers).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credential import Credential
from app.models.marketplace import (
    MarketplaceInstallation,
    MarketplaceItem,
    MarketplaceVersion,
)
from app.models.skill import Skill
from app.models.user import User
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


_SRT_REQUIREMENT = {
    "key": "srt_login",
    "definition_key": "srt_account",
    "required": True,
    "label": "SRT 계정",
    "fields": ["username", "password"],
    "injection": "env",
    "scope": "user",
    "env_map": {"SRT_USERNAME": "username", "SRT_PASSWORD": "password"},
}


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


def _seed_snapshot(storage_path: Path) -> None:
    """Sync helper for ``asyncio.to_thread`` (ASYNC240 — async fns avoid
    pathlib I/O methods directly)."""

    storage_path.mkdir(parents=True, exist_ok=True)
    (storage_path / "SKILL.md").write_text(
        ("---\nname: srt-booker\ndescription: SRT auto-book\nversion: '0.1.0'\n---\n\nbody\n"),
        encoding="utf-8",
    )


async def _make_published_skill_item(
    db: AsyncSession,
    *,
    storage_path: Path,
    requirements: list[dict] | None = None,
    owner_id: uuid.UUID | None = None,
) -> tuple[MarketplaceItem, MarketplaceVersion]:
    """Build a published skill item + version with snapshot bytes on disk."""

    # Snapshot is a directory containing a SKILL.md file (package kind).
    import asyncio

    await asyncio.to_thread(_seed_snapshot, storage_path)

    item = MarketplaceItem(
        id=uuid.uuid4(),
        resource_type="skill",
        owner_user_id=owner_id,
        is_system=False,
        is_listed=True,
        name="SRT Booker",
        slug=f"srt-booker-{uuid.uuid4().hex[:8]}",
        description="SRT auto-book",
        visibility="public",
        status="published",
        moderation_status="approved",
        source_kind="user",
    )
    db.add(item)
    await db.flush()

    version = MarketplaceVersion(
        id=uuid.uuid4(),
        item_id=item.id,
        version_label="0.1.0",
        version_number=1,
        resource_type="skill",
        payload_kind="skill_package",
        payload={"kind": "package", "name": "srt-booker", "version": "0.1.0"},
        storage_path=str(storage_path),
        content_hash="deadbeef" * 8,
        size_bytes=512,
        credential_requirements=requirements,
        execution_profile={"support_level": "experimental"},
    )
    db.add(version)
    await db.flush()

    item.latest_version_id = version.id
    item.published_at = _now()
    await db.flush()
    return item, version


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_creates_skill_and_installation(
    client: AsyncClient, db: AsyncSession, tmp_path: Path
) -> None:
    await _ensure_test_user(db)

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        version_dir = tmp_path / "marketplace-versions" / "v1"
        item, _version = await _make_published_skill_item(
            db, storage_path=version_dir, requirements=None
        )
        await db.commit()

        r = await client.post(
            f"/api/marketplace/items/{item.id}/install",
            json={"install_mode": "reuse_or_update"},
        )

        assert r.status_code == 201, r.text
        body = r.json()
        assert body["resource_type"] == "skill"
        assert body["install_status"] == "active"
        assert body["installed_skill_id"] is not None
        assert body["is_dirty"] is False

        # Installed skill row exists and filesystem snapshot was copied.
        skill = await db.get(Skill, uuid.UUID(body["installed_skill_id"]))
        assert skill is not None
        assert skill.user_id == TEST_USER_ID
        assert skill.source_marketplace_item_id == item.id
        assert skill.origin_kind in ("community", "imported_by_me")
        # Filesystem assertions go via a sync helper to satisfy ASYNC240.
        import asyncio

        from app.storage.paths import resolve_data_path

        assert skill.storage_path is not None
        exists, has_skill_md = await asyncio.to_thread(
            _check_skill_files, resolve_data_path(skill.storage_path)
        )
        assert exists  # package storage_path is dir
        assert has_skill_md


def _check_skill_files(p: Path) -> tuple[bool, bool]:
    return p.exists(), (p / "SKILL.md").exists()


@pytest.mark.asyncio
async def test_install_with_missing_required_binding_marks_needs_setup(
    client: AsyncClient, db: AsyncSession, tmp_path: Path
) -> None:
    await _ensure_test_user(db)

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        version_dir = tmp_path / "v1"
        item, _ = await _make_published_skill_item(
            db,
            storage_path=version_dir,
            requirements=[_SRT_REQUIREMENT],
        )
        await db.commit()

        r = await client.post(
            f"/api/marketplace/items/{item.id}/install",
            json={"install_mode": "reuse_or_update"},
        )

    assert r.status_code == 201, r.text
    assert r.json()["install_status"] == "needs_setup"


@pytest.mark.asyncio
async def test_install_with_binding_supplied_is_active(
    client: AsyncClient, db: AsyncSession, tmp_path: Path
) -> None:
    await _ensure_test_user(db)
    cred = Credential(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        definition_key="srt_account",
        name="my-srt",
        data_encrypted="opaque",
        key_id="kv1",
        field_keys=[],
        is_shared=False,
        is_system=False,
        status="active",
    )
    db.add(cred)
    await db.flush()

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        version_dir = tmp_path / "v1"
        item, _ = await _make_published_skill_item(
            db,
            storage_path=version_dir,
            requirements=[_SRT_REQUIREMENT],
        )
        await db.commit()

        r = await client.post(
            f"/api/marketplace/items/{item.id}/install",
            json={
                "install_mode": "reuse_or_update",
                "credential_bindings": {"srt_login": str(cred.id)},
            },
        )

    assert r.status_code == 201, r.text
    assert r.json()["install_status"] == "active"


@pytest.mark.asyncio
async def test_install_reuse_returns_existing_installation(
    client: AsyncClient, db: AsyncSession, tmp_path: Path
) -> None:
    await _ensure_test_user(db)

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        version_dir = tmp_path / "v1"
        item, _ = await _make_published_skill_item(db, storage_path=version_dir, requirements=None)
        await db.commit()

        r1 = await client.post(
            f"/api/marketplace/items/{item.id}/install",
            json={"install_mode": "reuse_or_update"},
        )
        r2 = await client.post(
            f"/api/marketplace/items/{item.id}/install",
            json={"install_mode": "reuse_or_update"},
        )

    assert r1.status_code == 201
    assert r2.status_code == 201
    # Same installation row — reuse_or_update is idempotent.
    assert r1.json()["id"] == r2.json()["id"]


@pytest.mark.asyncio
async def test_delete_installation_soft_then_hard(
    client: AsyncClient, db: AsyncSession, tmp_path: Path
) -> None:
    await _ensure_test_user(db)

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        version_dir = tmp_path / "v1"
        item, _ = await _make_published_skill_item(db, storage_path=version_dir, requirements=None)
        await db.commit()

        r1 = await client.post(
            f"/api/marketplace/items/{item.id}/install",
            json={"install_mode": "reuse_or_update"},
        )
        installation_id = r1.json()["id"]
        skill_id = uuid.UUID(r1.json()["installed_skill_id"])

        # Soft delete — installation marked uninstalled, skill row stays.
        r2 = await client.delete(f"/api/marketplace/installations/{installation_id}")
        assert r2.status_code == 204

    inst = await db.get(MarketplaceInstallation, uuid.UUID(installation_id))
    assert inst is not None
    assert inst.install_status == "uninstalled"
    assert (await db.get(Skill, skill_id)) is not None

    # Hard delete — cascades into the skill row + filesystem.
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        r3 = await client.delete(
            f"/api/marketplace/installations/{installation_id}",
            params={"delete_resource": True},
        )
    assert r3.status_code == 204

    # Re-query — both gone.
    rows = (
        await db.execute(
            select(MarketplaceInstallation).where(
                MarketplaceInstallation.id == uuid.UUID(installation_id)
            )
        )
    ).all()
    assert rows == []
    assert (await db.get(Skill, skill_id)) is None


@pytest.mark.asyncio
async def test_install_404_for_unknown_item(client: AsyncClient) -> None:
    r = await client.post(
        f"/api/marketplace/items/{uuid.uuid4()}/install",
        json={"install_mode": "reuse_or_update"},
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "MARKETPLACE_ITEM_NOT_FOUND"
