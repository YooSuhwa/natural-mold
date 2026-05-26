"""ADR-017 Slice C — marketplace publish flow integration smoke.

Covers the happy paths sat called out in the M6 brief:

* fresh publish creates an item + immutable version + publication link
* dedup: re-publishing an unchanged skill returns the same version row
* secret_scan rejects ``.env``/``.pem``/``sk-...`` payloads → 400
* PATCH metadata edits stick, no version mutation
* ACL replace + delete enforces ≥1 row for restricted
* disable_item → status='disabled', is_listed=False

Module boundary: this file lives in ``tests/`` which is normally
베조스's area. The brief explicitly carved out
``tests/test_marketplace_*.py`` for the marketplace surface tests in
Slice A/B; Slice C publish belongs to the same family. Keep new files
narrowly named so a future split into ``tests/marketplace/`` is clean.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.marketplace import (
    MarketplaceItem,
    MarketplacePublicationLink,
    MarketplaceVersion,
)
from app.models.skill import Skill
from app.models.user import User
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


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


def _skill_md(name: str = "demo") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        'description: "demo skill"\n'
        'version: "1.0.0"\n'
        "---\n\n"
        "# Demo body\n"
    )


def _zip_with(files: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            zf.writestr(name, data)
    return buf.getvalue()


async def _make_user_skill(
    db: AsyncSession, *, tmp_path: Path, name: str = "demo"
) -> Skill:
    """Create a real on-disk skill via the service so storage_path is
    populated (publish service reads it)."""

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name=name,
            slug=None,
            description="demo",
            content=_skill_md(name),
        )
    await db.flush()
    return skill


# ---------------------------------------------------------------------------
# Publish — happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_creates_item_version_and_publication_link(
    client: AsyncClient, db: AsyncSession, tmp_path: Path
) -> None:
    await _ensure_test_user(db)
    skill = await _make_user_skill(db, tmp_path=tmp_path)
    await db.commit()

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        r = await client.post(
            f"/api/marketplace/items/from-skill/{skill.id}",
            json={
                "visibility": "public",
                "name": "Demo Marketplace Item",
                "description": "demo",
                "tags": ["sample"],
                "categories": ["dev"],
                "release_notes": "first cut",
            },
        )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "published"
    assert body["visibility"] == "public"
    # Public publish starts unlisted (Spec §0.1) — super_user toggles.
    assert body["is_listed"] is False
    assert body["latest_version"] is not None
    version_id = uuid.UUID(body["latest_version"]["id"])

    version = await db.get(MarketplaceVersion, version_id)
    assert version is not None
    assert version.version_number == 1

    item = await db.get(MarketplaceItem, uuid.UUID(body["id"]))
    assert item is not None
    assert item.owner_user_id == TEST_USER_ID
    assert item.latest_version_id == version_id

    # Publication link wired up.
    from sqlalchemy import select

    link = (
        await db.execute(
            select(MarketplacePublicationLink).where(
                MarketplacePublicationLink.item_id == item.id
            )
        )
    ).scalar_one_or_none()
    assert link is not None
    assert link.source_skill_id == skill.id


@pytest.mark.asyncio
async def test_publish_dedup_reuses_version_for_unchanged_skill(
    client: AsyncClient, db: AsyncSession, tmp_path: Path
) -> None:
    await _ensure_test_user(db)
    skill = await _make_user_skill(db, tmp_path=tmp_path)
    await db.commit()

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        r1 = await client.post(
            f"/api/marketplace/items/from-skill/{skill.id}",
            json={"visibility": "public", "name": "Dedup Demo"},
        )
        assert r1.status_code == 201
        item_id = r1.json()["id"]
        v1 = r1.json()["latest_version"]["id"]

        r2 = await client.post(
            f"/api/marketplace/items/{item_id}/versions/from-skill/{skill.id}",
            json={"release_notes": "no-op"},
        )

    assert r2.status_code == 200
    v2 = r2.json()["latest_version"]["id"]
    # Same content hash → dedup → same version id.
    assert v1 == v2


@pytest.mark.asyncio
async def test_publish_restricted_requires_acl(
    client: AsyncClient, db: AsyncSession, tmp_path: Path
) -> None:
    await _ensure_test_user(db)
    skill = await _make_user_skill(db, tmp_path=tmp_path)
    await db.commit()

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        r = await client.post(
            f"/api/marketplace/items/from-skill/{skill.id}",
            json={"visibility": "restricted", "name": "Restricted", "acl_user_ids": []},
        )

    # Pydantic ``model_validator`` raises ValueError ⇒ 422 from the
    # FastAPI exception handler.
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# Secret scan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_rejects_env_file(
    client: AsyncClient, db: AsyncSession, tmp_path: Path
) -> None:
    """``.skill`` upload with a ``.env`` file is blocked by secret_scan."""

    await _ensure_test_user(db)
    zip_bytes = _zip_with(
        {
            "SKILL.md": _skill_md("with-env"),
            ".env": "OPENAI_API_KEY=sk-abc\n",
        }
    )

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        r = await client.post(
            "/api/skills/upload",
            files={"file": ("demo.skill", zip_bytes, "application/zip")},
        )

    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "MARKETPLACE_SECRET_DETECTED"


@pytest.mark.asyncio
async def test_upload_rejects_openai_key_in_content(
    client: AsyncClient, db: AsyncSession, tmp_path: Path
) -> None:
    """Content scanner catches an embedded OpenAI key even with a clean
    filename."""

    await _ensure_test_user(db)
    leaked = "OPENAI=" + "sk-" + "A" * 30  # 30+ chars → matches \bsk-…
    zip_bytes = _zip_with(
        {
            "SKILL.md": _skill_md("leaky"),
            "scripts/run.py": leaked,
        }
    )

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        r = await client.post(
            "/api/skills/upload",
            files={"file": ("demo.skill", zip_bytes, "application/zip")},
        )

    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "MARKETPLACE_SECRET_DETECTED"


@pytest.mark.asyncio
async def test_upload_allows_placeholder_sk_example(
    client: AsyncClient, db: AsyncSession, tmp_path: Path
) -> None:
    """``sk-example`` placeholder (no word-boundary match) must NOT
    block. Pins the Bezos OI-4 fix on word boundaries + min length."""

    await _ensure_test_user(db)
    zip_bytes = _zip_with(
        {
            "SKILL.md": _skill_md("safe"),
            "README.md": "Use OPENAI_API_KEY=sk-example for tests.\n",
        }
    )

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        r = await client.post(
            "/api/skills/upload",
            files={"file": ("demo.skill", zip_bytes, "application/zip")},
        )

    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# Patch + ACL + disable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_item_metadata(
    client: AsyncClient, db: AsyncSession, tmp_path: Path
) -> None:
    await _ensure_test_user(db)
    skill = await _make_user_skill(db, tmp_path=tmp_path)
    await db.commit()

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        r1 = await client.post(
            f"/api/marketplace/items/from-skill/{skill.id}",
            json={"visibility": "public", "name": "P1"},
        )
        item_id = r1.json()["id"]
        version_before = r1.json()["latest_version"]["id"]

        r2 = await client.patch(
            f"/api/marketplace/items/{item_id}",
            json={"description": "new desc", "tags": ["alpha", "beta"]},
        )

    assert r2.status_code == 200, r2.text
    assert r2.json()["description"] == "new desc"
    assert set(r2.json()["tags"] or []) == {"alpha", "beta"}
    # PATCH must not bump the version.
    assert r2.json()["latest_version"]["id"] == version_before


@pytest.mark.asyncio
async def test_disable_blocks_install(
    client: AsyncClient, db: AsyncSession, tmp_path: Path
) -> None:
    await _ensure_test_user(db)
    skill = await _make_user_skill(db, tmp_path=tmp_path)
    await db.commit()

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        r1 = await client.post(
            f"/api/marketplace/items/from-skill/{skill.id}",
            json={"visibility": "public", "name": "ToDisable"},
        )
        item_id = r1.json()["id"]

        r2 = await client.post(
            f"/api/marketplace/items/{item_id}/disable"
        )

    assert r2.status_code == 200
    assert r2.json()["status"] == "disabled"
    assert r2.json()["is_listed"] is False
