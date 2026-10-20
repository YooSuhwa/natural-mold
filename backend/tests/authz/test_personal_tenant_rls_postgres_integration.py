"""Personal-company signup on real PostgreSQL with RLS forced and a non-owner app role.

The signup request session is the production ``tenant_session(None)`` on the app role;
the new company, organization, memberships and audit rows are written through the
identity scope, and the new account can then act inside its own company only.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import database
from app.config import settings
from app.database import tenant_session
from app.dependencies import get_db
from app.main import create_app
from tests.authz.i06_rls_lab import RlsLab, async_url, rls_lab

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def lab() -> Iterator[RlsLab]:
    with rls_lab() as value:
        yield value


@pytest.fixture(autouse=True)
def personal_rls_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "db_rls_enabled", True)
    monkeypatch.setattr(settings, "cookie_secure", False)
    monkeypatch.setattr(settings, "deployment_mode", "multi_tenant")
    monkeypatch.setattr(settings, "registration_policy", "personal_tenant")
    monkeypatch.setattr(settings, "allow_first_user_as_admin", False)


@pytest.fixture
async def client(lab: RlsLab) -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(async_url(lab.app_url))
    maker = async_sessionmaker(engine, class_=database.ShieldedAsyncSession, expire_on_commit=False)
    app = create_app()

    async def request_session() -> AsyncGenerator[AsyncSession, None]:
        async with tenant_session(None, maker=maker) as db:  # production get_db semantics
            yield db

    app.dependency_overrides[get_db] = request_session
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://personal.test") as http:
            yield http
    finally:
        await engine.dispose()


async def _signup(client: AsyncClient, email: str) -> UUID:
    response = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "correct horse", "name": "Personal"},
    )
    assert response.status_code == 201, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return UUID(response.json()["user"]["id"])


def _company_of(lab: RlsLab, user_id: UUID) -> UUID:
    with lab.admin.connect() as connection:
        rows = list(
            connection.scalars(
                sa.text("SELECT tenant_id FROM tenant_members WHERE user_id = :u"), {"u": user_id}
            )
        )
    assert len(rows) == 1
    return rows[0]


async def test_personal_signup_commits_its_company_under_forced_rls(
    lab: RlsLab, client: AsyncClient
) -> None:
    user_id = await _signup(client, "rls-personal@example.com")
    company = _company_of(lab, user_id)
    with lab.admin.connect() as connection:
        kind = connection.scalar(
            sa.text("SELECT settings->>'kind' FROM tenants WHERE id = :t"), {"t": company}
        )
        roles = set(
            connection.scalars(
                sa.text("SELECT role FROM tenant_role_grants WHERE user_id = :u"), {"u": user_id}
            )
        )
        actions = set(
            connection.scalars(
                sa.text("SELECT action FROM audit_events WHERE actor_user_id = :u"), {"u": user_id}
            )
        )
    assert kind == "personal"
    assert roles == {"admin"}
    assert {"tenant.create", "tenant.member_add", "auth.register"} <= actions
    # The new account resolves its own company context through the app role.
    scoped = await client.get("/api/access-requests", params={"box": "outbox"})
    assert scoped.status_code == 200, scoped.text


def _platform_model(lab: RlsLab) -> UUID:
    model_id = uuid4()
    with lab.admin.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO models (id, provider, model_name, display_name, is_visible,"
                " created_at) VALUES (:id, 'openai', 'gpt-test', 'GPT Test', true, now())"
            ),
            {"id": model_id},
        )
    return model_id


async def test_two_personal_companies_cannot_read_each_others_agents(
    lab: RlsLab, client: AsyncClient
) -> None:
    model_id = _platform_model(lab)
    owner = await _signup(client, "rls-owner@example.com")
    created = await client.post(
        "/api/agents",
        json={"name": "Private agent", "system_prompt": "hi", "model_id": str(model_id)},
    )
    assert created.status_code in (200, 201), created.text
    agent_id = created.json()["id"]
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    stranger = await _signup(client, "rls-stranger@example.com")
    assert _company_of(lab, owner) != _company_of(lab, stranger)
    peek = await client.get(f"/api/agents/{agent_id}")
    assert peek.status_code == 404
    listed = await client.get("/api/agents")
    assert listed.status_code == 200
    assert agent_id not in {item["id"] for item in listed.json()}
