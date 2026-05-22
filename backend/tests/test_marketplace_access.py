"""Marketplace access control matrix tests (PRD §12, Spec §12).

Two layers:

1. **Pure-function predicate matrix** — ``can_view_item`` / ``can_install_item``
   / ``can_manage_item`` exercised across (owner, ACL user, unrelated user,
   super_user) × (private, restricted, public, unlisted, system, disabled).
2. **Router-level enumeration-oracle gate** — ``GET /api/marketplace/items/{id}``
   returns the same ``404 MARKETPLACE_ITEM_NOT_FOUND`` for "does not exist"
   AND "exists but hidden" (rules/security.md). 403 leaks visibility.

Note on auth fixtures: the default ``client`` fixture in conftest overrides
``get_current_user`` to a hard-coded super_user. To exercise the regular-user
gate we build a fresh ASGI app with our own ``CurrentUser`` override.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

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
from app.marketplace.access import (
    can_install_item,
    can_manage_item,
    can_view_item,
    is_owner,
)
from app.models.marketplace import (
    MarketplaceItem,
    MarketplaceItemACL,
    MarketplaceVersion,
)
from tests.conftest import TestSession, override_get_db

# Stable user identities for the matrix.
OWNER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
ACL_USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
UNRELATED_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
SUPER_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")


def _user(uid: uuid.UUID, *, is_super: bool = False) -> CurrentUser:
    return CurrentUser(
        id=uid,
        email=f"{uid.hex[:8]}@test.com",
        name="U",
        is_super_user=is_super,
    )


def _build_item(
    *,
    visibility: str,
    status: str = "published",
    owner_id: uuid.UUID | None = OWNER_ID,
    is_system: bool = False,
    is_listed: bool = False,
    acl: list[uuid.UUID] | None = None,
    acl_permission: str = "install",
) -> MarketplaceItem:
    """In-memory item for predicate tests — never persisted to DB."""

    item = MarketplaceItem(
        id=uuid.uuid4(),
        resource_type="skill",
        owner_user_id=owner_id,
        is_system=is_system,
        is_listed=is_listed,
        name="probe",
        slug=f"probe-{uuid.uuid4().hex[:6]}",
        visibility=visibility,
        status=status,
        moderation_status="approved",
    )
    item.acl_entries = [
        MarketplaceItemACL(
            item_id=item.id,
            user_id=u,
            permission=acl_permission,
        )
        for u in (acl or [])
    ]
    return item


# ===========================================================================
# Predicate matrix — pure-function unit tests
# ===========================================================================


class TestCanViewItem:
    """View visibility matrix per Spec §12."""

    def test_owner_sees_private_item(self) -> None:
        item = _build_item(visibility="private", status="draft")
        assert can_view_item(item, _user(OWNER_ID)) is True

    def test_unrelated_user_does_not_see_private_item(self) -> None:
        item = _build_item(visibility="private", status="draft")
        assert can_view_item(item, _user(UNRELATED_ID)) is False

    def test_super_user_always_sees_item(self) -> None:
        item = _build_item(visibility="private", status="draft")
        assert can_view_item(item, _user(SUPER_ID, is_super=True)) is True

    def test_acl_user_sees_restricted_item(self) -> None:
        item = _build_item(visibility="restricted", acl=[ACL_USER_ID])
        assert can_view_item(item, _user(ACL_USER_ID)) is True

    def test_non_acl_user_does_not_see_restricted_item(self) -> None:
        item = _build_item(visibility="restricted", acl=[ACL_USER_ID])
        assert can_view_item(item, _user(UNRELATED_ID)) is False

    def test_public_published_visible_to_anyone(self) -> None:
        item = _build_item(visibility="public", status="published")
        assert can_view_item(item, _user(UNRELATED_ID)) is True

    def test_public_draft_hidden_from_non_owner(self) -> None:
        """Visibility=public but status=draft must still hide. Otherwise a
        user could publish a half-finished draft and have it leak."""

        item = _build_item(visibility="public", status="draft")
        assert can_view_item(item, _user(UNRELATED_ID)) is False
        # Owner can still see their own draft.
        assert can_view_item(item, _user(OWNER_ID)) is True

    def test_unlisted_published_visible_to_anyone_with_link(self) -> None:
        item = _build_item(visibility="unlisted", status="published")
        assert can_view_item(item, _user(UNRELATED_ID)) is True

    def test_system_item_visible_to_everyone(self) -> None:
        item = _build_item(
            visibility="system", is_system=True, owner_id=None
        )
        assert can_view_item(item, _user(UNRELATED_ID)) is True

    def test_disabled_item_hidden_from_unrelated_user(self) -> None:
        item = _build_item(
            visibility="public", status="disabled", is_listed=True
        )
        assert can_view_item(item, _user(UNRELATED_ID)) is False
        # super_user keeps view rights (admin moderation flow).
        assert can_view_item(item, _user(SUPER_ID, is_super=True)) is True
        # Owner can still see their own disabled item.
        assert can_view_item(item, _user(OWNER_ID)) is True


class TestCanInstallItem:
    """Install requires view + the 'install' bit on restricted ACL."""

    def test_acl_install_bit_required_for_restricted(self) -> None:
        view_only_item = _build_item(
            visibility="restricted",
            acl=[ACL_USER_ID],
            acl_permission="view",
        )
        # View-only ACL can SEE but not install.
        assert can_view_item(view_only_item, _user(ACL_USER_ID)) is True
        assert can_install_item(view_only_item, _user(ACL_USER_ID)) is False

        install_item = _build_item(
            visibility="restricted",
            acl=[ACL_USER_ID],
            acl_permission="install",
        )
        assert can_install_item(install_item, _user(ACL_USER_ID)) is True

    def test_unrelated_user_cannot_install_private(self) -> None:
        item = _build_item(visibility="private", status="draft")
        assert can_install_item(item, _user(UNRELATED_ID)) is False

    def test_disabled_item_install_blocked_for_non_super_user(self) -> None:
        item = _build_item(
            visibility="public", status="disabled", is_listed=True
        )
        assert can_install_item(item, _user(UNRELATED_ID)) is False
        # Owner can re-install their own disabled draft for testing.
        # super_user can install for rescue.
        assert can_install_item(item, _user(SUPER_ID, is_super=True)) is True

    def test_public_published_installable_by_any_authenticated_user(
        self,
    ) -> None:
        item = _build_item(visibility="public", status="published")
        assert can_install_item(item, _user(UNRELATED_ID)) is True


class TestCanManageItem:
    """Manage = publish/ACL/disable. Owner or super_user only."""

    def test_owner_can_manage(self) -> None:
        item = _build_item(visibility="private", status="draft")
        assert can_manage_item(item, _user(OWNER_ID)) is True

    def test_super_user_can_manage(self) -> None:
        item = _build_item(visibility="private", status="draft")
        assert can_manage_item(item, _user(SUPER_ID, is_super=True)) is True

    def test_unrelated_user_cannot_manage(self) -> None:
        item = _build_item(visibility="public", status="published")
        assert can_manage_item(item, _user(UNRELATED_ID)) is False

    def test_acl_view_alone_does_not_grant_manage(self) -> None:
        item = _build_item(
            visibility="restricted",
            acl=[ACL_USER_ID],
            acl_permission="install",
        )
        # 'install' ACL is not 'manage' ACL.
        assert can_manage_item(item, _user(ACL_USER_ID)) is False


class TestIsOwner:
    def test_owner_match(self) -> None:
        item = _build_item(visibility="private")
        assert is_owner(item, _user(OWNER_ID)) is True
        assert is_owner(item, _user(UNRELATED_ID)) is False

    def test_system_item_has_no_owner(self) -> None:
        item = _build_item(
            visibility="system", is_system=True, owner_id=None
        )
        # Even when the caller passes the OWNER_ID, ownership predicate
        # must be False (system items have no owner row).
        assert is_owner(item, _user(OWNER_ID)) is False


# ===========================================================================
# Router-level enumeration-oracle test (CRITICAL — Phase 1 출시 게이트)
# ===========================================================================


async def _client_for_user(user: CurrentUser) -> AsyncClient:
    """Build an ASGI client whose ``get_current_user`` returns ``user``."""

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db

    async def _user_override() -> CurrentUser:
        return user

    app.dependency_overrides[get_current_user] = _user_override
    app.dependency_overrides[get_current_user_optional] = _user_override

    async def _no_csrf() -> None:
        return None

    app.dependency_overrides[verify_csrf] = _no_csrf

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _seed_private_item(
    db: AsyncSession, *, owner_id: uuid.UUID
) -> MarketplaceItem:
    """Insert a private item owned by ``owner_id``. Status=draft so only
    the owner can see it (Spec §12)."""

    item = MarketplaceItem(
        id=uuid.uuid4(),
        resource_type="skill",
        owner_user_id=owner_id,
        is_system=False,
        is_listed=False,
        name="hidden",
        slug=f"hidden-{uuid.uuid4().hex[:6]}",
        visibility="private",
        status="draft",
    )
    db.add(item)
    await db.commit()
    return item


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Standalone session for seeding (the ``db`` fixture is reused below)."""

    async with TestSession() as session:
        yield session


class TestEnumerationOracleSafety:
    """Phase 1 출시 게이트 — Access control.

    ``rules/security.md``: "존재 여부와 권한 검증의 외부 응답을 통일".
    Anything but ``404 MARKETPLACE_ITEM_NOT_FOUND`` from these endpoints
    is a leak.
    """

    @pytest.mark.asyncio
    async def test_unauthorized_user_sees_404_not_403_for_private_item(
        self, db_session: AsyncSession
    ) -> None:
        item = await _seed_private_item(db_session, owner_id=OWNER_ID)

        async with await _client_for_user(_user(UNRELATED_ID)) as client:
            resp = await client.get(f"/api/marketplace/items/{item.id}")

        # NOT 403 — that would tell the attacker the item exists.
        assert resp.status_code == 404, resp.text
        body = resp.json()
        assert body.get("error", {}).get("code") == "MARKETPLACE_ITEM_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_nonexistent_item_returns_same_404_payload(
        self, db_session: AsyncSession
    ) -> None:
        """Truly missing item vs. hidden-from-user item must return the
        SAME body so the response shape itself is not an oracle."""

        # Seed a hidden item so the schema/route is exercised, then ask
        # for two ids: the hidden one and a random unused one.
        hidden = await _seed_private_item(db_session, owner_id=OWNER_ID)
        random_id = uuid.uuid4()

        async with await _client_for_user(_user(UNRELATED_ID)) as client:
            r_hidden = await client.get(f"/api/marketplace/items/{hidden.id}")
            r_missing = await client.get(f"/api/marketplace/items/{random_id}")

        assert r_hidden.status_code == 404
        assert r_missing.status_code == 404
        # Same envelope structure.
        assert r_hidden.json() == r_missing.json()

    @pytest.mark.asyncio
    async def test_owner_can_see_their_own_private_item(
        self, db_session: AsyncSession
    ) -> None:
        """Sanity check the 404 isn't over-reaching."""

        item = await _seed_private_item(db_session, owner_id=OWNER_ID)

        async with await _client_for_user(_user(OWNER_ID)) as client:
            resp = await client.get(f"/api/marketplace/items/{item.id}")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == str(item.id)
        # Publication summary가 owner 본인에게 정상 노출되는지만 검증한다.
        # 카탈로그 서비스는 publication_link가 있을 때만 published_* 상태를
        # 표시한다 (orphaned owner — source skill을 삭제한 케이스 — 에서는
        # not_published로 떨어뜨려야 자기 publish 백업본을 다시 install 가능).
        # 이 fixture는 publication_link 없이 draft row만 만들므로
        # "draft" 또는 "not_published" 둘 다 valid 한 owner view.
        assert body["publication_summary"]["state"] in ("draft", "not_published")

    @pytest.mark.asyncio
    async def test_versions_list_uses_same_404_envelope(
        self, db_session: AsyncSession
    ) -> None:
        """``GET /items/{id}/versions`` shares the enumeration gate."""

        item = await _seed_private_item(db_session, owner_id=OWNER_ID)

        async with await _client_for_user(_user(UNRELATED_ID)) as client:
            resp = await client.get(
                f"/api/marketplace/items/{item.id}/versions"
            )

        assert resp.status_code == 404
        assert (
            resp.json().get("error", {}).get("code")
            == "MARKETPLACE_ITEM_NOT_FOUND"
        )

    @pytest.mark.asyncio
    async def test_version_detail_unreachable_when_parent_hidden(
        self, db_session: AsyncSession
    ) -> None:
        """A version's parent item visibility gates the version itself.

        Build a private item + a version row directly, then probe the
        version detail endpoint as an unrelated user."""

        item = await _seed_private_item(db_session, owner_id=OWNER_ID)
        version = MarketplaceVersion(
            id=uuid.uuid4(),
            item_id=item.id,
            version_label="v1",
            version_number=1,
            resource_type="skill",
            payload_kind="skill_package",
            payload={},
            content_hash="0" * 64,
            size_bytes=0,
        )
        db_session.add(version)
        await db_session.commit()

        async with await _client_for_user(_user(UNRELATED_ID)) as client:
            resp = await client.get(f"/api/marketplace/versions/{version.id}")

        assert resp.status_code == 404
        # ``MARKETPLACE_VERSION_NOT_FOUND`` (or item — both 404, both opaque).
        code = resp.json().get("error", {}).get("code") or ""
        assert code.startswith("MARKETPLACE_") and code.endswith("_NOT_FOUND")
