"""M9 — End-to-end marketplace scenarios (PRD §10.1~§10.7).

Each scenario walks the **public HTTP surface** for a complete user flow.
Unlike the per-module integration tests (``test_marketplace_install.py``,
``test_marketplace_publish.py``), these tests cross multiple endpoints
and at least one user identity boundary so the assertion is
*end-to-end* rather than *unit*.

Multi-user pattern: the default ``client`` fixture hard-codes a single
super_user. To exercise role boundaries (publisher vs installer vs
super_user), we build a fresh ASGI client whose ``get_current_user``
returns the requested identity — same approach as
``test_marketplace_access.py:_client_for_user``.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_current_user_optional,
    get_db,
    verify_csrf,
)
from app.main import create_app
from app.models.marketplace import (
    MarketplaceItem,
    MarketplaceVersion,
)
from app.models.skill import Skill
from app.models.user import User
from app.skills import service as skill_service
from tests.conftest import TestSession, override_get_db

# ---------------------------------------------------------------------------
# Helpers — multi-user clients + seed data
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _user(uid: uuid.UUID, *, is_super: bool = False) -> CurrentUser:
    return CurrentUser(
        id=uid,
        email=f"{uid.hex[:8]}@test.com",
        name="U",
        is_super_user=is_super,
    )


async def _client_for(user: CurrentUser) -> AsyncClient:
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db

    async def _override() -> CurrentUser:
        return user

    app.dependency_overrides[get_current_user] = _override
    app.dependency_overrides[get_current_user_optional] = _override

    async def _no_csrf() -> None:
        return None

    app.dependency_overrides[verify_csrf] = _no_csrf

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _seed_user(db: AsyncSession, uid: uuid.UUID, *, is_super: bool = False) -> None:
    existing = await db.get(User, uid)
    if existing is None:
        db.add(
            User(
                id=uid,
                email=f"{uid.hex[:8]}@test.com",
                name=f"U-{uid.hex[:4]}",
                hashed_password="h",
                is_active=True,
                is_super_user=is_super,
            )
        )
        await db.commit()


def _skill_md(name: str) -> str:
    return f'---\nname: {name}\ndescription: "demo"\nversion: "1.0.0"\n---\n\nBody\n'


def _zip_skill(name: str, *, with_secret: bool = False) -> bytes:
    """Build a minimal .skill package. ``with_secret=True`` embeds an
    ``.env`` so the secret_scan integration tests can use it too."""

    files: dict[str, str] = {"SKILL.md": _skill_md(name)}
    if with_secret:
        files[".env"] = "OPENAI_API_KEY=sk-realisticlooking1234567890\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, body in files.items():
            zf.writestr(path, body.encode("utf-8"))
    return buf.getvalue()


def _seed_snapshot_dir(root: Path, slug: str = "snap") -> Path:
    """Materialize an on-disk skill snapshot the install path can copy."""

    storage = root / "marketplace-versions" / slug
    storage.mkdir(parents=True, exist_ok=True)
    (storage / "SKILL.md").write_text(
        f"---\nname: {slug}\ndescription: demo\nversion: '0.1.0'\n---\n\nbody\n",
        encoding="utf-8",
    )
    return storage


async def _seed_published_item(
    db: AsyncSession,
    *,
    owner_id: uuid.UUID | None,
    storage_path: Path,
    visibility: str = "public",
    is_listed: bool = True,
    is_system: bool = False,
    source_kind: str = "user",
    name: str | None = None,
) -> tuple[MarketplaceItem, MarketplaceVersion]:
    """Insert a published item + its immutable version row."""

    item = MarketplaceItem(
        id=uuid.uuid4(),
        resource_type="skill",
        owner_user_id=owner_id,
        is_system=is_system,
        is_listed=is_listed,
        name=name or f"item-{uuid.uuid4().hex[:6]}",
        slug=f"slug-{uuid.uuid4().hex[:8]}",
        description="seeded",
        visibility=visibility,
        status="published",
        moderation_status="approved",
        source_kind=source_kind,
        published_at=_now(),
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
        payload={"kind": "package", "name": item.name},
        storage_path=str(storage_path),
        content_hash="d" * 64,
        size_bytes=128,
    )
    db.add(version)
    await db.flush()
    item.latest_version_id = version.id
    await db.commit()
    return item, version


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSession() as session:
        yield session


# ===========================================================================
# Scenario 10.1 — built-in skill (k-skill) install + agent attach
# ===========================================================================


class TestScenario_10_1_BuiltInSkillInstall:
    """PRD §10.1: 사용자가 Marketplace에서 ``korean-spell-check`` 같은
    built-in skill을 찾아 install → 자기 ``skills`` row 생성 → agent 설정에서
    선택."""

    @pytest.mark.asyncio
    async def test_built_in_kskill_install_to_user_owned_skill(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        user_id = uuid.uuid4()
        await _seed_user(db_session, user_id)
        # System k-skill — built-in, no credential requirements.
        snap = _seed_snapshot_dir(tmp_path, "korean-spell-check")
        item, _v = await _seed_published_item(
            db_session,
            owner_id=None,
            storage_path=snap,
            visibility="system",
            is_listed=True,
            is_system=True,
            source_kind="k-skill",
            name="korean-spell-check",
        )

        with patch.object(skill_service.settings, "data_root", str(tmp_path)):
            async with await _client_for(_user(user_id)) as client:
                # Catalog returns the system item.
                catalog = await client.get("/api/marketplace/items?visibility=system")
                assert catalog.status_code == 200
                catalog_slugs = {r["slug"] for r in catalog.json()}
                assert item.slug in catalog_slugs

                # Install creates a user-owned Skill row.
                inst = await client.post(
                    f"/api/marketplace/items/{item.id}/install",
                    json={"install_mode": "reuse_or_update"},
                )

        assert inst.status_code == 201, inst.text
        body = inst.json()
        assert body["install_status"] == "active"
        assert body["installed_skill_id"] is not None

        # Installed Skill row exists, owned by the installer (not the
        # system item), with marketplace lineage populated.
        skill = await db_session.get(Skill, uuid.UUID(body["installed_skill_id"]))
        assert skill is not None
        assert skill.user_id == user_id, (
            "installed skill must belong to the installer, not the system"
        )
        assert skill.source_marketplace_item_id == item.id
        assert skill.origin_kind == "built_in_k_skill", (
            "Spec §6 — k-skill installs must carry built_in_k_skill origin"
        )


# ===========================================================================
# Scenario 10.2 — credential required → needs_setup → binding → active
# ===========================================================================


class TestScenario_10_2_CredentialRequiredFlow:
    """PRD §10.2: ``srt-booking`` 설치 시 SRT 계정 credential 필요.
    binding 없이 install_missing_credentials=needs_setup → install_status='needs_setup'.
    """

    @pytest.mark.asyncio
    async def test_install_with_missing_required_credential_marks_needs_setup(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        user_id = uuid.uuid4()
        await _seed_user(db_session, user_id)
        snap = _seed_snapshot_dir(tmp_path, "srt-booking")
        item, version = await _seed_published_item(
            db_session,
            owner_id=None,
            storage_path=snap,
            visibility="system",
            is_system=True,
            source_kind="k-skill",
        )
        # Attach a required SRT credential requirement.
        version.credential_requirements = [
            {
                "key": "srt_account",
                "definition_key": "srt_account",
                "required": True,
                "label": "SRT login",
                "fields": ["username", "password"],
                "injection": "env",
                "scope": "user",
                "env_map": {
                    "username": "KSKILL_SRT_ID",
                    "password": "KSKILL_SRT_PASSWORD",
                },
            }
        ]
        await db_session.commit()

        with patch.object(skill_service.settings, "data_root", str(tmp_path)):
            async with await _client_for(_user(user_id)) as client:
                resp = await client.post(
                    f"/api/marketplace/items/{item.id}/install",
                    json={
                        "install_mode": "reuse_or_update",
                        "install_missing_credentials": "needs_setup",
                    },
                )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["install_status"] == "needs_setup", (
            "missing required binding must produce needs_setup, not active"
        )


# ===========================================================================
# Scenario 10.3 — user publishes own skill → another user installs
# ===========================================================================


class TestScenario_10_3_PublishThenInstallByPeer:
    """PRD §10.3: 사용자 A가 자기 skill을 public publish → 사용자 B가 install."""

    @pytest.mark.asyncio
    async def test_publisher_b_can_install_a_published_skill(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        user_a = uuid.uuid4()
        user_b = uuid.uuid4()
        await _seed_user(db_session, user_a)
        await _seed_user(db_session, user_b)

        with patch.object(skill_service.settings, "data_root", str(tmp_path)):
            # User A creates a text skill + publishes it.
            skill = await skill_service.create_text_skill(
                db_session,
                user_id=user_a,
                name="Shared Skill",
                slug=None,
                description="from user A",
                content=_skill_md("shared-skill"),
            )
            await db_session.commit()

            async with await _client_for(_user(user_a)) as client_a:
                pub = await client_a.post(
                    f"/api/marketplace/items/from-skill/{skill.id}",
                    json={
                        "visibility": "public",
                        "name": "Shared Item",
                        "description": "for the world",
                    },
                )
                assert pub.status_code == 201, pub.text
                item_id = pub.json()["id"]
                # Public publish starts unlisted — Spec §7.
                assert pub.json()["is_listed"] is False

            # User B can fetch via direct ID (unlisted public, link-only).
            async with await _client_for(_user(user_b)) as client_b:
                detail = await client_b.get(f"/api/marketplace/items/{item_id}")
                assert detail.status_code == 200, detail.text
                # User B installs.
                inst = await client_b.post(
                    f"/api/marketplace/items/{item_id}/install",
                    json={"install_mode": "reuse_or_update"},
                )

        assert inst.status_code == 201, inst.text
        body = inst.json()
        assert body["install_status"] == "active"
        # User B's installed skill is user-owned, lineage links to item.
        new_skill = await db_session.get(Skill, uuid.UUID(body["installed_skill_id"]))
        assert new_skill is not None
        assert new_skill.user_id == user_b
        assert new_skill.source_marketplace_item_id == uuid.UUID(item_id)
        # Origin reflects "I imported it" (not k-skill, not created_by_me).
        assert new_skill.origin_kind in ("community", "imported_by_me")


# ===========================================================================
# Scenario 10.4 — restricted ACL: only allowed users see/install
# ===========================================================================


class TestScenario_10_4_RestrictedACL:
    """PRD §10.4: visibility=restricted + ACL [user B].
    User B → install OK. User C (unrelated) → 404 (enumeration oracle)."""

    @pytest.mark.asyncio
    async def test_restricted_acl_grants_and_denies(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        """Bezos M9 finding 2026-05-19 (RESOLVED 2026-05-19):
        ``install_service.install_item`` originally used
        ``db.get(MarketplaceItem, ...)`` without eager-loading
        ``acl_entries``; ``can_install_item`` then triggered a lazy load
        inside async context → MissingGreenlet → 500. 젠슨 patched the
        service to use ``select(...).options(selectinload(acl_entries))``
        so non-ACL install attempts now collapse to 404 cleanly.

        This test was strict-xfail-pinned during the gap so the fix was
        auto-detected via XPASS. The xfail decorator has been removed —
        the assertion is now the canonical Phase 1 출시 게이트 #1
        (enumeration oracle) regression guard.
        """
        user_a = uuid.uuid4()  # publisher
        user_b = uuid.uuid4()  # ACL allowed
        user_c = uuid.uuid4()  # unrelated
        for u in (user_a, user_b, user_c):
            await _seed_user(db_session, u)

        snap = _seed_snapshot_dir(tmp_path, "restricted")
        item, _v = await _seed_published_item(
            db_session,
            owner_id=user_a,
            storage_path=snap,
            visibility="restricted",
        )
        # Insert ACL entry for user B.
        from app.models.marketplace import MarketplaceItemACL

        db_session.add(MarketplaceItemACL(item_id=item.id, user_id=user_b, permission="install"))
        await db_session.commit()

        with patch.object(skill_service.settings, "data_root", str(tmp_path)):
            # User C — must 404 on both detail and install (oracle uniform).
            async with await _client_for(_user(user_c)) as client_c:
                r_detail_c = await client_c.get(f"/api/marketplace/items/{item.id}")
                r_install_c = await client_c.post(
                    f"/api/marketplace/items/{item.id}/install",
                    json={"install_mode": "reuse_or_update"},
                )
            assert r_detail_c.status_code == 404
            assert r_install_c.status_code == 404, (
                "non-ACL install must collapse to 404, not 403 — enumeration oracle leak"
            )

            # User B — full flow.
            async with await _client_for(_user(user_b)) as client_b:
                r_detail_b = await client_b.get(f"/api/marketplace/items/{item.id}")
                assert r_detail_b.status_code == 200
                r_install_b = await client_b.post(
                    f"/api/marketplace/items/{item.id}/install",
                    json={"install_mode": "reuse_or_update"},
                )
        assert r_install_b.status_code == 201, r_install_b.text


# ===========================================================================
# Scenario 10.5 — update available: overwrite vs install_new_copy
# ===========================================================================


class TestScenario_10_5_UpdateStrategies:
    """PRD §10.5: 같은 item에 새 version publish → install된 사용자의
    installation.update_available=True. 사용자가 ``strategy='overwrite'``
    또는 ``install_new_copy``를 선택."""

    @pytest.mark.asyncio
    async def test_update_strategy_overwrite_swaps_version(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        user_id = uuid.uuid4()
        await _seed_user(db_session, user_id)

        snap_v1 = _seed_snapshot_dir(tmp_path, "v1")
        item, v1 = await _seed_published_item(
            db_session, owner_id=None, storage_path=snap_v1, is_system=True
        )

        with patch.object(skill_service.settings, "data_root", str(tmp_path)):
            async with await _client_for(_user(user_id)) as client:
                inst = await client.post(
                    f"/api/marketplace/items/{item.id}/install",
                    json={"install_mode": "reuse_or_update"},
                )
                assert inst.status_code == 201
                installation_id = inst.json()["id"]
                installed_skill_id = inst.json()["installed_skill_id"]

                # Publish a new version (simulate: insert v2 + bump latest).
                snap_v2 = _seed_snapshot_dir(tmp_path, "v2")
                v2 = MarketplaceVersion(
                    id=uuid.uuid4(),
                    item_id=item.id,
                    version_label="0.2.0",
                    version_number=2,
                    resource_type="skill",
                    payload_kind="skill_package",
                    payload={"kind": "package"},
                    storage_path=str(snap_v2),
                    content_hash="e" * 64,
                    size_bytes=256,
                )
                db_session.add(v2)
                await db_session.flush()
                fresh_item = await db_session.get(MarketplaceItem, item.id)
                assert fresh_item is not None
                fresh_item.latest_version_id = v2.id
                await db_session.commit()

                # Apply overwrite strategy.
                upd = await client.post(
                    f"/api/marketplace/installations/{installation_id}/update",
                    json={"strategy": "overwrite"},
                )

        assert upd.status_code == 200, upd.text
        body = upd.json()
        # Installation now points at v2.
        assert body["version_id"] == str(v2.id)
        assert body["installed_skill_id"] == installed_skill_id
        assert body["is_dirty"] is False  # overwrite resets dirty


# ===========================================================================
# Scenario 10.6 — publication status visible on owner's skill detail
# ===========================================================================


class TestScenario_10_6_PublicationStatusBadge:
    """PRD §10.6: owner의 ``GET /api/skills/{id}`` 응답에
    ``publication_summary.state`` 가 정확히 표시."""

    @pytest.mark.asyncio
    async def test_publication_summary_reflects_visibility_change(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        user_id = uuid.uuid4()
        await _seed_user(db_session, user_id)

        with patch.object(skill_service.settings, "data_root", str(tmp_path)):
            skill = await skill_service.create_text_skill(
                db_session,
                user_id=user_id,
                name="Pub Probe",
                slug=None,
                description="probe",
                content=_skill_md("pub-probe"),
            )
            await db_session.commit()

            async with await _client_for(_user(user_id)) as client:
                # Before publish — not_published.
                r0 = await client.get(f"/api/skills/{skill.id}")
                assert r0.status_code == 200
                assert r0.json()["publication_summary"]["state"] == "not_published"

                # Publish as private — published_private.
                pub = await client.post(
                    f"/api/marketplace/items/from-skill/{skill.id}",
                    json={"visibility": "private", "name": "Pub Probe"},
                )
                assert pub.status_code == 201, pub.text

                r1 = await client.get(f"/api/skills/{skill.id}")
                assert r1.status_code == 200
                state = r1.json()["publication_summary"]["state"]
                assert state == "published_private", f"expected published_private, got {state!r}"


# ===========================================================================
# Scenario 10.7 — super_user lists a public-unlisted item
# ===========================================================================


class TestScenario_10_7_SuperUserListsItem:
    """PRD §10.7: super_user가 public+is_listed=False 항목을 카탈로그에
    노출시키는 흐름. Slice C의 admin listed 토글이 아직 없을 수도 있으므로
    DB-level 토글 + 동일 사용자 시점 catalog 응답 변화로 검증."""

    @pytest.mark.asyncio
    async def test_super_user_can_promote_unlisted_to_listed(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        user_id = uuid.uuid4()
        publisher_id = uuid.uuid4()
        await _seed_user(db_session, user_id)
        await _seed_user(db_session, publisher_id)

        snap = _seed_snapshot_dir(tmp_path, "unlisted")
        item, _v = await _seed_published_item(
            db_session,
            owner_id=publisher_id,
            storage_path=snap,
            visibility="public",
            is_listed=False,  # awaiting moderation
        )

        async with await _client_for(_user(user_id)) as client:
            # Default catalog excludes unlisted-public.
            r0 = await client.get("/api/marketplace/items?visibility=public&is_listed=true")
            assert item.slug not in {row["slug"] for row in r0.json()}

        # Super_user flips the flag (admin endpoint may not exist yet —
        # exercise the DB invariant directly to confirm the catalog
        # respects ``is_listed`` immediately).
        fresh_item = await db_session.get(MarketplaceItem, item.id)
        assert fresh_item is not None
        fresh_item.is_listed = True
        await db_session.commit()

        async with await _client_for(_user(user_id)) as client:
            r1 = await client.get("/api/marketplace/items?visibility=public&is_listed=true")
            assert item.slug in {row["slug"] for row in r1.json()}, (
                "after super_user listing, item must appear in default catalog view"
            )
