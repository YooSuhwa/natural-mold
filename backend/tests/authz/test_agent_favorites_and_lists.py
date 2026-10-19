"""D-02: shared agent lists, per-user favorites and the d02 preference migration."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import create_engine, select, text

from app.models.agent import Agent
from app.models.agent_user_preference import AgentUserPreference
from tests.authz.agent_route_fixture import (
    ME,
    OTHER_USER_ID,
    authz_override,
    enforce,
    seed_agent,
    shadow,
)
from tests.conftest import TEST_USER_ID, TestSession

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


async def test_enforce_lists_shared_agents_confirmed_by_openfga(
    client: AsyncClient, test_app: FastAPI
):
    mine = await seed_agent(owner=TEST_USER_ID, name="mine")
    shared = await seed_agent(owner=OTHER_USER_ID, name="shared", grant_to_me=True)
    ungranted = await seed_agent(owner=OTHER_USER_ID, name="ungranted")
    allowed = [(ME, "can_run", uuid.UUID(i)) for i in (mine, shared, ungranted)]
    service = enforce(*allowed, (ME, "can_view_config", uuid.UUID(mine)))
    with authz_override(test_app, service):
        listed = (await client.get("/api/agents")).json()
        summary = (await client.get("/api/agents/summary")).json()
    by_id = {item["id"]: item for item in listed}
    assert set(by_id) == {mine, shared}  # never a candidate without a grant
    assert by_id[mine]["system_prompt"] == "secret prompt"
    assert by_id[shared]["view"] == "runner" and "system_prompt" not in by_id[shared]
    assert {row["id"]: row["my_permissions"] for row in summary} == {
        mine: ["can_run", "can_view_config"],
        shared: ["can_run"],
    }


async def test_owner_favorite_stays_on_the_legacy_column(client: AsyncClient):
    agent_id = await seed_agent(owner=TEST_USER_ID)
    response = await client.patch(f"/api/agents/{agent_id}/favorite")
    assert response.status_code == 200 and response.json()["is_favorite"] is True
    async with TestSession() as db:
        agent = await db.get(Agent, uuid.UUID(agent_id))
        prefs = (await db.scalars(select(AgentUserPreference))).all()
    assert agent is not None and agent.is_favorite is True
    assert prefs == []  # owners never write preference rows (column is the truth until P8)


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
async def test_put_after_patch_keeps_the_owner_favorite(
    client: AsyncClient, test_app: FastAPI, mode: str
):
    agent_id = await seed_agent(owner=TEST_USER_ID)
    agent = uuid.UUID(agent_id)
    perms = ("can_run", "can_view_config", "can_edit")
    service = shadow([]) if mode == "shadow" else enforce(*((ME, p, agent) for p in perms))
    with authz_override(test_app, service):
        patched = await client.patch(f"/api/agents/{agent_id}/favorite")
        put = await client.put(f"/api/agents/{agent_id}", json={"name": "renamed"})
        got = await client.get(f"/api/agents/{agent_id}")
    assert [r.json()["is_favorite"] for r in (patched, put, got)] == [True, True, True]
    assert put.json()["my_permissions"][:2] == ["can_run", "can_view_config"]


async def test_shadow_list_and_summary_serialize_my_permissions(
    client: AsyncClient, test_app: FastAPI
):
    agent_id = await seed_agent(owner=TEST_USER_ID)
    with authz_override(test_app, shadow([])):
        listed = (await client.get("/api/agents")).json()
        summary = (await client.get("/api/agents/summary")).json()
    everything = ["can_run", "can_view_config", "can_clone", "can_edit"]
    everything += ["can_share", "can_deploy", "can_delete"]
    assert [(i["id"], i["my_permissions"]) for i in listed] == [(agent_id, everything)]
    assert [(i["id"], i["my_permissions"]) for i in summary] == [(agent_id, everything)]
    assert "system_prompt" in listed[0] and "view" not in listed[0]


async def test_shared_favorite_only_changes_the_viewers_preference(
    client: AsyncClient, test_app: FastAPI
):
    agent_id = await seed_agent(owner=OTHER_USER_ID, grant_to_me=True)
    with authz_override(test_app, enforce((ME, "can_run", uuid.UUID(agent_id)))):
        response = await client.patch(f"/api/agents/{agent_id}/favorite")
    assert response.status_code == 200 and response.json()["is_favorite"] is True
    async with TestSession() as db:
        agent = await db.get(Agent, uuid.UUID(agent_id))
        prefs = (await db.scalars(select(AgentUserPreference))).all()
    assert agent is not None and agent.is_favorite is False
    assert [(p.user_id, p.is_favorite) for p in prefs] == [(TEST_USER_ID, True)]


def test_d02_migration_creates_an_empty_table_and_downgrades() -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app.database import Base

    spec = importlib.util.spec_from_file_location(
        "d02_mig", VERSIONS / "d02_agent_user_preferences.py"
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "c04_outbox_sequence"
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        prior = [t for t in Base.metadata.sorted_tables if t.name != "agent_user_preferences"]
        Base.metadata.create_all(connection, tables=prior)
        connection.execute(text("PRAGMA foreign_keys=OFF"))
        for n, favorite in ((1, True), (2, False)):
            connection.execute(
                text(
                    "INSERT INTO agents (id, user_id, org_id, tenant_id, name, system_prompt,"
                    " runtime_name, identity_mode, model_id, status, runtime_profile,"
                    " is_favorite, created_at, updated_at) VALUES (:id, :u, :o, :t, 'a', 'p',"
                    " :rn, 'fixed', :m, 'active', 'standard', :f, CURRENT_TIMESTAMP,"
                    " CURRENT_TIMESTAMP)"
                ),
                {
                    "id": uuid.UUID(int=n).hex,
                    "u": uuid.UUID(int=9).hex,
                    "o": uuid.UUID(int=8).hex,
                    "t": uuid.UUID(int=7).hex,
                    "rn": f"agent_{n}",
                    "m": uuid.UUID(int=6).hex,
                    "f": favorite,
                },
            )
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
            rows = connection.execute(
                text("SELECT agent_id, user_id, is_favorite FROM agent_user_preferences")
            ).all()
            assert rows == []  # owners stay on agents.is_favorite until P8
            migration.downgrade()
            assert "agent_user_preferences" not in {
                r[0] for r in connection.execute(text("SELECT name FROM sqlite_master"))
            }
    engine.dispose()


pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")
