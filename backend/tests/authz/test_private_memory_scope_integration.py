"""Actual m83 memory HTTP writes and proposal approval preserve exact private scope."""

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.memory import MemoryProposal, MemoryRecord
from app.models.user import User
from tests.authz.native_company_selection import select_second_company
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("other_company", [False, True])
async def test_native_memory_record_and_proposal_http_writes(
    monkeypatch: pytest.MonkeyPatch,
    other_company: bool,
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-memory@example.com",
                "name": "Memory fixture",
                "password": "native-memory-password",
            },
        )
        assert registered.status_code == 201, registered.text
        headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
        if other_company:
            await select_second_company(stack, headers, "native-memory@example.com")
        created = await stack.client.post(
            "/api/memories",
            headers=headers,
            json={"scope": "user", "content": "Native memory fixture"},
        )
        assert created.status_code == 201, created.text
        proposed = await stack.client.post(
            "/api/memory-proposals",
            headers=headers,
            json={"scope": "user", "content": "Native proposal fixture"},
        )
        assert proposed.status_code == 201, proposed.text
        approved = await stack.client.post(
            f"/api/memory-proposals/{proposed.json()['id']}/approve",
            headers=headers,
        )
        assert approved.status_code == 200, approved.text
        async with stack.sessions() as db:
            user = await db.scalar(select(User).where(User.email == "native-memory@example.com"))
            records = list((await db.scalars(select(MemoryRecord))).all())
            proposals = list((await db.scalars(select(MemoryProposal))).all())
            assert user is not None and len(records) == 2 and len(proposals) == 1
            for row in [*records, *proposals]:
                assert row.user_id == user.id and row.org_id == user.last_active_org_id
                assert row.tenant_id is not None
            assert proposals[0].status == "approved"
        listed = await stack.client.get("/api/memories")
        assert listed.status_code == 200 and len(listed.json()) == 2
