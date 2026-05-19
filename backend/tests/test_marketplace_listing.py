"""Phase 1 출시 게이트 — Listing 승인 (PRD §7, Spec §10.1).

새 service 동작 (M2.5 course correction):

* **기본 ``GET /api/marketplace/items``** (no ``?is_listed``) — Spec §10.1:
  owner OR system OR (public+published+is_listed=True) OR (restricted+ACL).
  ``visibility=unlisted``과 ``is_listed=False`` public은 list 결과에서 제외.
* **``?is_listed=true``** — explicit, default와 동일 결과 (명시적 토글 가드용).
* **``?is_listed=false``** — moderation 큐. owner의 미승인 + super_user에게
  공개 미승인 후보. ``visibility=unlisted``은 여전히 제외.
* **``visibility=unlisted``** — list에서 항상 제외. detail(``GET /items/{id}``)은
  authenticated user 모두 접근 가능 (direct-link sharing).
* **super_user** — 모든 항목 + 모든 ``is_listed`` 값 노출 (관리 권한).

이 파일은 list 결과의 **포함/제외 매트릭스**를 가드한다. detail 접근 + ACL
permission 행렬은 ``test_marketplace_access.py``가 담당.
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
from app.models.marketplace import MarketplaceItem
from tests.conftest import TestSession, override_get_db

REGULAR_ID = uuid.UUID("aaaa1111-aaaa-1111-aaaa-1111aaaa1111")
SUPER_ID = uuid.UUID("bbbb2222-bbbb-2222-bbbb-2222bbbb2222")
SEED_OWNER_ID = uuid.UUID("cccc3333-cccc-3333-cccc-3333cccc3333")


def _user(uid: uuid.UUID, *, is_super: bool = False) -> CurrentUser:
    return CurrentUser(
        id=uid,
        email=f"{uid.hex[:8]}@test.com",
        name="U",
        is_super_user=is_super,
    )


async def _client_for_user(user: CurrentUser) -> AsyncClient:
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


def _make_item(
    *,
    slug: str,
    visibility: str,
    status: str = "published",
    is_listed: bool = False,
    is_system: bool = False,
    owner_id: uuid.UUID | None = None,
) -> MarketplaceItem:
    return MarketplaceItem(
        id=uuid.uuid4(),
        resource_type="skill",
        owner_user_id=owner_id,
        is_system=is_system,
        is_listed=is_listed,
        name=slug,
        slug=slug,
        visibility=visibility,
        status=status,
    )


@pytest.fixture
async def seeded(
    db_session: AsyncSession,
) -> AsyncGenerator[dict[str, MarketplaceItem | uuid.UUID], None]:
    """Seed a fixed catalog covering the listed/unlisted matrix.

    Owner is ``SEED_OWNER_ID`` so the regular-user (``REGULAR_ID``) and
    super-user (``SUPER_ID``) clients can probe non-owner behaviour.
    """

    items: dict[str, MarketplaceItem | uuid.UUID] = {
        "public_listed": _make_item(
            slug="public-listed",
            visibility="public",
            is_listed=True,
            owner_id=SEED_OWNER_ID,
        ),
        "public_unlisted": _make_item(
            slug="public-unlisted",
            visibility="public",
            is_listed=False,
            owner_id=SEED_OWNER_ID,
        ),
        "unlisted_vis": _make_item(
            slug="unlisted-vis",
            visibility="unlisted",
            is_listed=False,
            owner_id=SEED_OWNER_ID,
        ),
        "system": _make_item(
            slug="system-item",
            visibility="system",
            is_system=True,
            is_listed=False,
        ),
        "regular_owned_private": _make_item(
            slug="regular-private",
            visibility="private",
            status="draft",
            is_listed=False,
            owner_id=REGULAR_ID,
        ),
        "regular_owned_unlisted": _make_item(
            slug="regular-unlisted",
            visibility="unlisted",
            status="published",
            is_listed=False,
            owner_id=REGULAR_ID,
        ),
    }
    for v in items.values():
        # Skip the sentinel keys (none here, but defensive).
        if isinstance(v, MarketplaceItem):
            db_session.add(v)
    await db_session.commit()
    yield items


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSession() as session:
        yield session


# ===========================================================================
# Default listing (no ?is_listed param) — Spec §10.1 catalog gate
# ===========================================================================


class TestDefaultListing:
    @pytest.mark.asyncio
    async def test_default_listing_excludes_unlisted_public_items(
        self, seeded: dict[str, MarketplaceItem | uuid.UUID]
    ) -> None:
        """Spec §10.1 (M2.5 course correction).

        Default ``GET /items`` for a non-owner regular user must surface
        ONLY listed-public + system + owner's own + restricted-ACL items.
        ``public+is_listed=False`` and ``visibility=unlisted`` are both
        excluded from the list (detail is still reachable by direct ID).

        Prior version (before M2.5) returned everything visible — that
        leaked unlisted-public items to anyone scrolling the catalog.
        """

        async with await _client_for_user(_user(REGULAR_ID)) as client:
            resp = await client.get("/api/marketplace/items")
        assert resp.status_code == 200, resp.text
        slugs = {row["slug"] for row in resp.json()}

        # Allowed: listed public + system + regular user's own items.
        assert "public-listed" in slugs
        assert "system-item" in slugs
        assert "regular-private" in slugs  # owner sees own draft
        assert "regular-unlisted" in slugs  # owner sees own unlisted

        # MUST be excluded — non-owner cannot see these in default list.
        assert "public-unlisted" not in slugs, (
            "Default catalog leaks public+is_listed=False item — "
            "Spec §10.1 위반 (M2.5 course correction 미적용)"
        )
        assert "unlisted-vis" not in slugs, (
            "visibility=unlisted must NEVER appear in list responses — "
            "Spec §7 'direct-link only' 위반"
        )

    @pytest.mark.asyncio
    async def test_owner_sees_own_unlisted_items_in_default_list(
        self, seeded: dict[str, MarketplaceItem | uuid.UUID]
    ) -> None:
        """Owner check — the ``owner_user_id == current_user.id`` clause
        in ``_base_catalog_query`` must override the unlisted exclusion.

        Otherwise the user can't find their own draft after closing the
        publish wizard."""

        async with await _client_for_user(_user(REGULAR_ID)) as client:
            resp = await client.get("/api/marketplace/items")
        assert resp.status_code == 200
        slugs = {row["slug"] for row in resp.json()}
        assert "regular-private" in slugs
        assert "regular-unlisted" in slugs

    @pytest.mark.asyncio
    async def test_super_user_default_list_includes_everything(
        self, seeded: dict[str, MarketplaceItem | uuid.UUID]
    ) -> None:
        """super_user always sees the full catalog — no default gating.

        Confirms the early-return branch in ``_base_catalog_query`` keeps
        the moderation surface complete without forcing ``?is_listed=false``."""

        async with await _client_for_user(
            _user(SUPER_ID, is_super=True)
        ) as client:
            resp = await client.get("/api/marketplace/items")
        assert resp.status_code == 200
        slugs = {row["slug"] for row in resp.json()}
        for required in (
            "public-listed",
            "public-unlisted",
            "unlisted-vis",
            "system-item",
            "regular-private",
            "regular-unlisted",
        ):
            assert required in slugs, (
                f"super_user list missing {required!r} — moderation view broken"
            )


# ===========================================================================
# Explicit ?is_listed=true|false
# ===========================================================================


class TestIsListedFilter:
    @pytest.mark.asyncio
    async def test_is_listed_true_filter_narrows_to_explicitly_listed_items(
        self, seeded: dict[str, MarketplaceItem | uuid.UUID]
    ) -> None:
        """``?is_listed=true`` is an explicit narrowing filter — intersects
        with the base visibility scope rather than replacing it.

        For a regular non-owner user that means: only items whose
        ``is_listed=True`` AND visible to the user remain. Owner items
        and system items with ``is_listed=False`` drop out. This is more
        restrictive than the default catalog view.
        """

        async with await _client_for_user(_user(REGULAR_ID)) as client:
            resp = await client.get(
                "/api/marketplace/items?is_listed=true"
            )
        assert resp.status_code == 200
        slugs = {row["slug"] for row in resp.json()}
        # Only public-listed has is_listed=True in the seed set.
        assert slugs == {"public-listed"}, (
            f"?is_listed=true must intersect with the visibility scope; "
            f"got {slugs}"
        )

    @pytest.mark.asyncio
    async def test_is_listed_false_filter_is_moderation_queue_for_super_user(
        self, seeded: dict[str, MarketplaceItem | uuid.UUID]
    ) -> None:
        """``?is_listed=false`` surfaces the moderation queue.

        super_user sees ALL unlisted public items (the items awaiting
        approval) plus their own unlisted ones. ``visibility=unlisted`` is
        still excluded from list responses (direct-link semantics).
        """

        async with await _client_for_user(
            _user(SUPER_ID, is_super=True)
        ) as client:
            resp = await client.get(
                "/api/marketplace/items?is_listed=false"
            )
        assert resp.status_code == 200
        slugs = {row["slug"] for row in resp.json()}

        # Moderation queue MUST surface public+is_listed=False.
        assert "public-unlisted" in slugs, (
            "Moderation view missing public+is_listed=False candidate — "
            "super_user cannot approve listings"
        )
        # Listed items excluded (the toggle is False).
        assert "public-listed" not in slugs

    @pytest.mark.asyncio
    async def test_is_listed_false_filter_surfaces_owner_unlisted(
        self, seeded: dict[str, MarketplaceItem | uuid.UUID]
    ) -> None:
        """Regular user with ``?is_listed=false`` should still see their
        own unlisted items (owner clause stays active)."""

        async with await _client_for_user(_user(REGULAR_ID)) as client:
            resp = await client.get(
                "/api/marketplace/items?is_listed=false"
            )
        assert resp.status_code == 200
        slugs = {row["slug"] for row in resp.json()}
        # Owner's own private+unlisted are surfaced — owner clause bypass
        # the is_listed condition.
        assert "regular-private" in slugs
        assert "regular-unlisted" in slugs


# ===========================================================================
# visibility filter
# ===========================================================================


class TestVisibilityFilter:
    @pytest.mark.asyncio
    async def test_visibility_unlisted_excluded_from_lists_but_reachable_by_id(
        self, seeded: dict[str, MarketplaceItem | uuid.UUID]
    ) -> None:
        """``visibility=unlisted`` is the "link-only" mode — list responses
        must NEVER include it, but detail (``GET /items/{id}``) keeps
        ungated access for any authenticated user (Spec §7).
        """

        target = seeded["unlisted_vis"]
        assert isinstance(target, MarketplaceItem)  # type narrow

        async with await _client_for_user(_user(REGULAR_ID)) as client:
            list_resp = await client.get(
                "/api/marketplace/items?visibility=unlisted"
            )
            detail_resp = await client.get(
                f"/api/marketplace/items/{target.id}"
            )

        assert list_resp.status_code == 200
        # No unlisted-visibility item should leak into the list, even
        # when the filter explicitly asks for them.
        list_slugs = {row["slug"] for row in list_resp.json()}
        assert "unlisted-vis" not in list_slugs

        # But direct ID access is the documented sharing mechanism.
        assert detail_resp.status_code == 200, detail_resp.text
        assert detail_resp.json()["slug"] == "unlisted-vis"

    @pytest.mark.asyncio
    async def test_visibility_system_filter_returns_system_items_only(
        self, seeded: dict[str, MarketplaceItem | uuid.UUID]
    ) -> None:
        async with await _client_for_user(_user(REGULAR_ID)) as client:
            resp = await client.get(
                "/api/marketplace/items?visibility=system"
            )
        assert resp.status_code == 200
        slugs = {row["slug"] for row in resp.json()}
        assert "system-item" in slugs
        # Public/unlisted items must not leak via this filter — visibility
        # filter narrows AFTER the base visibility gate.
        assert "public-listed" not in slugs
        assert "public-unlisted" not in slugs


# ===========================================================================
# Combined: visibility + is_listed (frontend catalog default)
# ===========================================================================


class TestCatalogDefaultView:
    @pytest.mark.asyncio
    async def test_visibility_public_plus_is_listed_true_is_clean_catalog(
        self, seeded: dict[str, MarketplaceItem | uuid.UUID]
    ) -> None:
        """The recommended frontend default
        ``GET /items?visibility=public&is_listed=true`` must return ONLY
        approved public items.

        This is the user-facing catalog promise: only items super_user
        toggled to listed appear by default."""

        async with await _client_for_user(_user(REGULAR_ID)) as client:
            resp = await client.get(
                "/api/marketplace/items?visibility=public&is_listed=true"
            )
        assert resp.status_code == 200
        slugs = {row["slug"] for row in resp.json()}
        assert slugs == {"public-listed"}, (
            f"Catalog default leaked items: {slugs - {'public-listed'}}"
        )

    @pytest.mark.asyncio
    async def test_unlisted_public_reachable_via_direct_id(
        self, seeded: dict[str, MarketplaceItem | uuid.UUID]
    ) -> None:
        """``is_listed=False`` is not a security boundary — it's a
        catalog-discoverability flag. Direct-link sharing must still
        work (PRD §7 "링크를 가진 사용자만 접근")."""

        target = seeded["public_unlisted"]
        assert isinstance(target, MarketplaceItem)

        async with await _client_for_user(_user(REGULAR_ID)) as client:
            resp = await client.get(f"/api/marketplace/items/{target.id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["slug"] == "public-unlisted"
        assert resp.json()["is_listed"] is False
