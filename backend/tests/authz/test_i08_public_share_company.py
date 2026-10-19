"""I-08 public share links check the link's company (plan 8.9 "공개 공유 링크"): a
suspended / scheduled-for-deletion company, or a link whose company is not its
organization's, reads as 404 on every public route — also after the snapshot cache
is warm. The token's company keys the public rate-limit bucket."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.models.organization import Tenant
from app.models.share_link import ShareLink
from app.services.share_rate_scope import public_share_rate_scope
from app.services.share_service import public_share_open
from tests.authz.d04_support import seed_scope
from tests.authz.test_d10_feedback_shares import _conversation
from tests.authz.unit_scope_fixture import TEST_TENANT_ID

OTHER_TENANT = uuid.uuid5(uuid.NAMESPACE_URL, "moldy/i08-share-other-company")
PUBLIC = ("", "/messages", "/artifacts")


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed_scope(db)


async def _token(client: AsyncClient, db: AsyncSession) -> str:
    conversation = await _conversation(db)
    response = await client.post(f"/api/conversations/{conversation}/share")
    return response.json()["share_token"]


async def _reads(client: AsyncClient, token: str) -> list[tuple[int, str | None]]:
    out = []
    for suffix in PUBLIC:
        response = await client.get(f"/api/shares/{token}{suffix}")
        code = response.json().get("error", {}).get("code") if response.status_code >= 400 else None
        out.append((response.status_code, code))
    return out


def _no_checkpoint():
    return patch(
        "app.routers.shares.chat_service.list_messages_from_checkpointer",
        new=AsyncMock(return_value=[]),
    )


@pytest.mark.parametrize("status", ["suspended", "deletion_scheduled"])
async def test_an_inactive_company_closes_its_links(
    client: AsyncClient, db: AsyncSession, status: str
) -> None:
    token = await _token(client, db)
    with _no_checkpoint():
        assert [code for code, _ in await _reads(client, token)] == [200, 200, 200]
        await db.execute(update(Tenant).where(Tenant.id == TEST_TENANT_ID).values(status=status))
        await db.commit()
        assert await _reads(client, token) == [(404, "SHARE_NOT_FOUND")] * len(PUBLIC)


async def test_a_link_from_another_company_is_404(client: AsyncClient, db: AsyncSession) -> None:
    token = await _token(client, db)
    db.add(Tenant(id=OTHER_TENANT, name="Other", slug="i08-other", secrets_namespace="i08-o"))
    await db.execute(
        update(ShareLink).where(ShareLink.share_token == token).values(tenant_id=OTHER_TENANT)
    )
    await db.commit()
    with _no_checkpoint():
        assert await _reads(client, token) == [(404, "SHARE_NOT_FOUND")] * len(PUBLIC)


def _request(token: str) -> Request:
    return Request({"type": "http", "path": f"/api/shares/{token}", "headers": []})


async def test_rate_scope_records_the_token_company(client: AsyncClient, db: AsyncSession) -> None:
    token = await _token(client, db)
    known, unknown = _request(token), _request("nope")
    await public_share_rate_scope(known, token, db)
    await public_share_rate_scope(unknown, "nope", db)
    assert known.state.share_tenant_id == TEST_TENANT_ID
    assert unknown.state.share_tenant_id is None


async def test_the_company_check_itself(client: AsyncClient, db: AsyncSession) -> None:
    """``public_share_open`` alone decides (the owner re-resolution only backs it up)."""
    token = await _token(client, db)
    link = await db.scalar(select(ShareLink).where(ShareLink.share_token == token))
    assert link is not None and await public_share_open(db, link)
    await db.execute(update(Tenant).where(Tenant.id == TEST_TENANT_ID).values(status="suspended"))
    assert not await public_share_open(db, link)
    await db.execute(update(Tenant).where(Tenant.id == TEST_TENANT_ID).values(status="active"))
    db.add(Tenant(id=OTHER_TENANT, name="Other", slug="i08-other", secrets_namespace="i08-o"))
    await db.flush()
    link.tenant_id = OTHER_TENANT
    assert not await public_share_open(db, link)
