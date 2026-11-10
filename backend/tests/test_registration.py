"""Who may register a tool, an MCP server, a skill or an agent (tool-governance-plan 3.6).

The rule table is written out value by value; the API cases act as a plain company member
and check the answer, what is stored and what the audit log says.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.registration import DEFAULT, STRICT, Rule, rules_from_settings
from app.models.agent import Agent
from app.models.audit_event import AuditEvent
from app.models.authz import ResourceGrant
from app.models.mcp_server import McpServer
from app.models.model import Model
from app.models.organization import OrganizationRoleGrant, Tenant
from app.models.skill import Skill
from app.models.template import Template
from app.models.tool import Tool
from app.schemas.agent import AgentCreate
from app.services import agent_service, audit_service
from tests.authz.d04_support import OTHER_USER_ID, add_server, add_tool, seed_scope
from tests.authz.d05_support import TEST_ORG_ID
from tests.authz.e01_support import add_agent, add_credential, add_skill, base, give_cap
from tests.authz.e09_support import MEMBER, TENANT, acting_as, actions, make_admin
from tests.authz.unit_scope_fixture import TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID, TestSession

REGISTRATION = f"{TENANT}/guardrails/registration"
GRANTED = {"who": "granted", "review": "off", "member_sharing": "shareable"}
PRIVATE = {"who": "members", "review": "off", "member_sharing": "private"}
OPEN = {"who": "members", "review": "off", "member_sharing": "shareable"}
SKILL_MD = '---\nname: demo\ndescription: "demo skill"\nversion: "1.0.0"\n---\n\n# Demo\n'
TOOL = {"definition_key": "http_request", "name": "n", "parameters": {}}
SERVER = {"name": "s", "transport": "streamable_http", "url": "https://mcp.example.com"}
IMPORT = {"mcpServers": {"one": {"transport": "streamable_http", "url": "https://a.example.com"}}}
SHARE = {"principal": {"type": "user", "id": str(OTHER_USER_ID)}, "level": "viewer"}


@pytest.fixture
async def _member(db: AsyncSession, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(audit_service, "async_session", TestSession)
    await seed_scope(db)
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        yield


async def _set(db: AsyncSession, value: Any) -> None:
    tenant = await db.get(Tenant, TEST_TENANT_ID)
    assert tenant is not None
    tenant.settings = {**(tenant.settings or {}), "registration": value}
    await db.commit()


async def _count(model: type) -> int:
    async with TestSession() as db:
        return await db.scalar(select(func.count()).select_from(model)) or 0


async def _saved() -> Any:
    async with TestSession() as db:
        tenant = await db.get(Tenant, TEST_TENANT_ID)
        assert tenant is not None
        return (tenant.settings or {}).get("registration")


def _meta(event: AuditEvent) -> dict[str, Any]:
    assert event.event_metadata is not None
    return event.event_metadata


def _refused(response: Response, capability: str) -> None:
    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert error["code"] == "capability_required"
    assert error["details"] == {"capability": capability}


# --- the rule table --------------------------------------------------------------------


def test_nothing_saved_is_the_default_for_every_kind() -> None:
    for settings in (None, {}, {"guardrails": {}}):
        assert rules_from_settings(settings) == {
            "mcp_server": DEFAULT,
            "tool": DEFAULT,
            "skill": DEFAULT,
            "agent": DEFAULT,
        }
    assert Rule("members", "off", "shareable") == DEFAULT


def test_a_saved_rule_is_read_per_kind_and_a_missing_kind_is_the_default() -> None:
    rules = rules_from_settings(
        {"registration": {"tool": {"who": "granted", "review": "on", "member_sharing": "private"}}}
    )
    assert rules["tool"] == Rule("granted", "on", "private")
    assert rules["mcp_server"] == Rule("members", "off", "shareable")
    assert rules["skill"] == Rule("members", "off", "shareable")


def test_review_never_applies_to_a_kind_without_a_registration_state() -> None:
    on = {"who": "members", "review": "on", "member_sharing": "shareable"}
    rules = rules_from_settings({"registration": dict.fromkeys(("tool", "skill", "agent"), on)})
    assert rules["tool"].review == "on"
    assert rules["skill"].review == "off"
    assert rules["agent"].review == "off"


@pytest.mark.parametrize(
    "raw",
    [
        "granted",
        ["tool"],
        {"tool": "granted"},
        {"tool": {"who": "nobody"}},
        {"tool": {"review": True}},
        {"tool": {"member_sharing": "team"}},
    ],
)
def test_a_value_that_cannot_be_read_is_the_strict_rule(raw: Any) -> None:
    assert Rule("granted", "on", "private") == STRICT
    assert rules_from_settings({"registration": raw})["tool"] == Rule("granted", "on", "private")


# --- who may create --------------------------------------------------------------------


async def _model(db: AsyncSession) -> Model:
    model = Model(provider="openai", model_name="gpt-reg", display_name="M")
    db.add(model)
    await db.commit()
    return model


async def _agent_body(db: AsyncSession) -> dict[str, Any]:
    return {"name": "A", "system_prompt": "p", "model_id": str((await _model(db)).id)}


@pytest.mark.asyncio
@pytest.mark.usefixtures("_member")
async def test_with_nothing_saved_every_member_creates(
    client: AsyncClient, db: AsyncSession
) -> None:
    assert (await client.post("/api/tools", json=TOOL)).status_code == 201
    assert (await client.post("/api/mcp-servers", json=SERVER)).status_code == 201
    skill = await client.post("/api/skills", json={"name": "S", "content": SKILL_MD})
    assert skill.status_code == 201, skill.text
    assert (await client.post("/api/agents", json=await _agent_body(db))).status_code == 201


@pytest.mark.asyncio
@pytest.mark.usefixtures("_member")
async def test_granted_refuses_a_member_on_every_tool_and_server_route(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _set(db, {"tool": GRANTED, "mcp_server": GRANTED})

    _refused(await client.post("/api/tools", json=TOOL), "cap_create_tool")
    _refused(await client.post("/api/mcp-servers", json=SERVER), "cap_register_mcp")
    _refused(await client.post("/api/mcp-servers/import", json=IMPORT), "cap_register_mcp")
    probe = {"transport": "streamable_http", "url": "https://mcp.example.com"}
    _refused(await client.post("/api/mcp-servers/probe", json=probe), "cap_register_mcp")

    assert await _count(Tool) == 0
    assert await _count(McpServer) == 0


@pytest.mark.asyncio
@pytest.mark.usefixtures("_member")
async def test_granted_refuses_a_member_on_skill_agent_and_builder_routes(
    client: AsyncClient, db: AsyncSession
) -> None:
    body = await _agent_body(db)
    await _set(db, {"skill": GRANTED, "agent": GRANTED})

    skill = await client.post("/api/skills", json={"name": "S", "content": SKILL_MD})
    _refused(skill, "cap_create_skill")
    _refused(await client.post("/api/agents", json=body), "cap_create_agent")
    _refused(await client.post("/api/builder", json={"user_request": "bot"}), "cap_create_agent")

    assert await _count(Skill) == 0
    assert await _count(Agent) == 0


@pytest.mark.asyncio
@pytest.mark.usefixtures("_member")
async def test_a_rule_for_one_kind_leaves_the_others_open(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _set(db, {"tool": GRANTED})

    _refused(await client.post("/api/tools", json=TOOL), "cap_create_tool")
    assert (await client.post("/api/mcp-servers", json=SERVER)).status_code == 201
    assert (await client.post("/api/agents", json=await _agent_body(db))).status_code == 201


@pytest.mark.asyncio
@pytest.mark.usefixtures("_member")
async def test_an_unreadable_setting_refuses_a_member(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _set(db, "everyone")

    _refused(await client.post("/api/tools", json=TOOL), "cap_create_tool")
    assert await _count(Tool) == 0


@pytest.mark.asyncio
@pytest.mark.usefixtures("_member")
async def test_a_capability_grant_admits_only_its_own_kind(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _set(db, {"tool": GRANTED, "mcp_server": GRANTED})
    await give_cap(db, "cap_create_tool")

    assert (await client.post("/api/tools", json=TOOL)).status_code == 201
    _refused(await client.post("/api/mcp-servers", json=SERVER), "cap_register_mcp")


@pytest.mark.asyncio
@pytest.mark.usefixtures("_member")
async def test_an_organization_builder_is_admitted(client: AsyncClient, db: AsyncSession) -> None:
    await _set(db, {"tool": GRANTED, "mcp_server": GRANTED})
    db.add(
        OrganizationRoleGrant(
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            role="builder",
            principal_type="user",
            principal_id=TEST_USER_ID,
        )
    )
    await db.commit()

    assert (await client.post("/api/tools", json=TOOL)).status_code == 201
    assert (await client.post("/api/mcp-servers", json=SERVER)).status_code == 201


@pytest.mark.asyncio
@pytest.mark.usefixtures("_member")
async def test_a_company_administrator_is_admitted(client: AsyncClient, db: AsyncSession) -> None:
    await _set(db, {"tool": GRANTED})
    await make_admin(db)

    assert (await client.post("/api/tools", json=TOOL)).status_code == 201


@pytest.mark.asyncio
@pytest.mark.usefixtures("_member")
async def test_a_marketplace_install_of_a_server_is_a_registration(
    client: AsyncClient, db: AsyncSession
) -> None:
    source, _ = await add_server(db, name="shared")
    published = await client.post(
        f"/api/marketplace/items/from-mcp/{source.id}",
        json={"visibility": "public", "name": "Shared MCP"},
    )
    assert published.status_code == 201, published.text
    install = f"/api/marketplace/items/{published.json()['id']}/install"
    await _set(db, {"mcp_server": GRANTED})

    _refused(await client.post(install, json={"install_mode": "new_copy"}), "cap_register_mcp")
    assert await _count(McpServer) == 1

    await give_cap(db, "cap_register_mcp")
    installed = await client.post(install, json={"install_mode": "new_copy"})
    assert installed.status_code == 201, installed.text
    assert await _count(McpServer) == 2


@pytest.mark.asyncio
async def test_a_template_makes_the_agent_without_a_skill_the_member_may_not_register(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.seed import default_marketplace_skills

    monkeypatch.setattr(default_marketplace_skills.settings, "data_root", str(tmp_path))
    await default_marketplace_skills.seed_default_marketplace_skills(db)
    await seed_scope(db)
    model = await _model(db)
    template = Template(
        name="tmpl-reg",
        category="개발",
        system_prompt="p",
        recommended_skill_slugs=["openwiki"],
    )
    db.add(template)
    await db.commit()
    await _set(db, {"skill": GRANTED})

    agent = await agent_service.create_agent(
        db,
        AgentCreate(name="Doc", system_prompt="p", model_id=model.id, template_id=template.id),
        unit_context(TEST_USER_ID),
    )

    assert agent.id is not None
    assert agent.skill_links == []
    owned = await db.scalar(
        select(func.count()).select_from(Skill).where(Skill.user_id == TEST_USER_ID)
    )
    assert owned == 0


# Rows the registration rule does not govern: seeds, the E2E fixture server and the hidden
# agent a skill builder session runs on (never listed, never attached by a person).
_UNGOVERNED = (
    "seed/",
    "models/",
    "e2e_mcp_apps_fixture_service.py",
    "skill_builder_hidden_agent.py",
)
_MAKES_A_ROW = re.compile(r"^\s*(?:\w+ = )?(?:Tool|McpServer|Skill|Agent)\($", re.MULTILINE)


def test_every_module_that_makes_a_governed_row_asks_the_registration_rule() -> None:
    app = Path(__file__).resolve().parents[1] / "app"
    makers = {
        str(path.relative_to(app))
        for path in app.rglob("*.py")
        if _MAKES_A_ROW.search(path.read_text(encoding="utf-8"))
        and not any(part in str(path.relative_to(app)) for part in _UNGOVERNED)
    }
    assert makers == {
        "marketplace/install/mcp.py",
        "marketplace/install/skill.py",
        "services/agent_blueprint_graph.py",
        "services/agent_creation_service.py",
        "services/builder_agent_finalization.py",
        "services/mcp_service.py",
        "services/tool_service.py",
        "skills/service.py",
    }
    for name in makers:
        source = (app / name).read_text(encoding="utf-8")
        rows = len(_MAKES_A_ROW.findall(source))
        asked = source.count("registration.admit(")
        assert asked >= 1, name
        # One question per row a module makes.
        assert asked >= rows, (name, rows, asked)


# --- member sharing --------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("_member")
async def test_private_keeps_what_a_member_registered_to_themselves(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db)
    server, _ = await add_server(db)
    agent = await add_agent(db)
    skill = await add_skill(db, owner=TEST_USER_ID, slug="mine")
    await _set(db, dict.fromkeys(("tool", "mcp_server", "agent", "skill"), PRIVATE))

    for kind, row, capability in (
        ("tool", tool, "cap_create_tool"),
        ("mcp_server", server, "cap_register_mcp"),
        ("agent", agent, "cap_create_agent"),
        ("skill", skill, "cap_create_skill"),
    ):
        _refused(await client.post(f"{base(kind, row.id)}/grants", json=SHARE), capability)
    assert await _count(ResourceGrant) == 0


@pytest.mark.asyncio
@pytest.mark.usefixtures("_member")
async def test_private_lets_a_capability_holder_share_and_leaves_other_kinds_alone(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db)
    server, _ = await add_server(db)
    credential = await add_credential(db, owner=TEST_USER_ID)
    await _set(db, {"tool": PRIVATE, "mcp_server": PRIVATE})
    await give_cap(db, "cap_create_tool")

    shared = await client.post(f"{base('tool', tool.id)}/grants", json=SHARE)
    assert shared.status_code == 201, shared.text
    refused = await client.post(f"{base('mcp_server', server.id)}/grants", json=SHARE)
    _refused(refused, "cap_register_mcp")
    use = {**SHARE, "level": "consumer"}
    other = await client.post(f"{base('credential', credential.id)}/grants", json=use)
    assert other.status_code == 201, other.text


@pytest.mark.asyncio
@pytest.mark.usefixtures("_member")
async def test_shareable_is_the_sharing_everyone_has_today(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool = await add_tool(db)
    await _set(db, {"tool": OPEN, "mcp_server": PRIVATE})

    shared = await client.post(f"{base('tool', tool.id)}/grants", json=SHARE)
    assert shared.status_code == 201, shared.text


# --- the settings API ------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("_member")
async def test_a_member_reads_the_defaults_and_cannot_write(client: AsyncClient) -> None:
    read = await client.get(REGISTRATION)
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["saved"] is False
    assert body["kinds"] == {
        "mcp_server": {**OPEN, "reviewable": True},
        "tool": {**OPEN, "reviewable": True},
        "skill": {**OPEN, "reviewable": False},
        "agent": {**OPEN, "reviewable": False},
    }

    write = await client.put(
        REGISTRATION, json={"kinds": {"tool": GRANTED}}, headers={"If-Match": read.headers["ETag"]}
    )
    assert write.status_code == 403, write.text
    assert await _saved() is None
    assert [e for e in await actions(TEST_TENANT_ID) if e.outcome == "success"] == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("_member")
async def test_an_administrator_saves_a_rule_and_the_change_is_audited(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    read = await client.get(REGISTRATION)
    rule = {"who": "granted", "review": "on", "member_sharing": "private"}

    write = await client.put(
        REGISTRATION, json={"kinds": {"tool": rule}}, headers={"If-Match": read.headers["ETag"]}
    )

    assert write.status_code == 200, write.text
    assert write.headers["ETag"] != read.headers["ETag"]
    body = write.json()
    assert body["saved"] is True
    assert body["kinds"]["tool"] == {**rule, "reviewable": True}
    assert body["kinds"]["skill"] == {**OPEN, "reviewable": False}
    assert await _saved() == {"mcp_server": OPEN, "tool": rule, "skill": OPEN, "agent": OPEN}

    (event,) = [e for e in await actions(TEST_TENANT_ID) if e.outcome == "success"]
    assert event.action == "registration.settings_updated"
    assert event.actor_user_id == TEST_USER_ID
    assert event.target_type == "tenant"
    assert _meta(event)["before"] == dict.fromkeys(("mcp_server", "tool", "skill", "agent"), OPEN)
    assert _meta(event)["after"]["tool"] == rule
    assert _meta(event)["after"]["agent"] == OPEN


@pytest.mark.asyncio
@pytest.mark.usefixtures("_member")
async def test_a_later_save_changes_one_kind_and_keeps_the_others(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    first = await client.put(
        REGISTRATION,
        json={"kinds": {"tool": GRANTED, "agent": PRIVATE}},
        headers={"If-Match": (await client.get(REGISTRATION)).headers["ETag"]},
    )
    assert first.status_code == 200, first.text

    second = await client.put(
        REGISTRATION, json={"kinds": {"tool": OPEN}}, headers={"If-Match": first.headers["ETag"]}
    )

    assert second.status_code == 200, second.text
    assert await _saved() == {"mcp_server": OPEN, "tool": OPEN, "skill": OPEN, "agent": PRIVATE}
    events = [e for e in await actions(TEST_TENANT_ID) if e.outcome == "success"]
    assert len(events) == 2
    changed = next(e for e in events if _meta(e)["before"]["tool"] == GRANTED)
    assert _meta(changed)["after"]["tool"] == OPEN
    assert _meta(changed)["after"]["agent"] == PRIVATE
    # ... and the saved rule is what the next creation reads.
    assert (await client.post("/api/tools", json=TOOL)).status_code == 201


@pytest.mark.asyncio
@pytest.mark.usefixtures("_member")
async def test_review_is_stored_off_for_a_kind_that_cannot_be_reviewed(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    on = {"who": "members", "review": "on", "member_sharing": "shareable"}

    write = await client.put(
        REGISTRATION,
        json={"kinds": {"skill": on, "mcp_server": on}},
        headers={"If-Match": (await client.get(REGISTRATION)).headers["ETag"]},
    )

    assert write.status_code == 200, write.text
    saved = await _saved()
    assert saved["skill"]["review"] == "off"
    assert saved["mcp_server"]["review"] == "on"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_member")
@pytest.mark.parametrize("stale", [None, 'W/"1"', "nonsense"])
async def test_a_stale_or_missing_version_changes_nothing(
    client: AsyncClient, db: AsyncSession, stale: str | None
) -> None:
    await make_admin(db)
    headers = {"If-Match": stale} if stale else {}

    write = await client.put(REGISTRATION, json={"kinds": {"tool": GRANTED}}, headers=headers)

    assert write.status_code == 409, write.text
    assert write.json()["error"]["code"] == "stale_version"
    assert await _saved() is None
    assert [e for e in await actions(TEST_TENANT_ID) if e.outcome == "success"] == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("_member")
@pytest.mark.parametrize(
    "kinds",
    [
        {"tool": {"who": "nobody"}},
        {"tool": {"review": "maybe"}},
        {"tool": {"member_sharing": "team"}},
        {"tool": {**GRANTED, "extra": 1}},
        {"credential": GRANTED},
    ],
)
async def test_an_invalid_rule_is_refused(
    client: AsyncClient, db: AsyncSession, kinds: dict[str, Any]
) -> None:
    await make_admin(db)
    etag = (await client.get(REGISTRATION)).headers["ETag"]

    write = await client.put(REGISTRATION, json={"kinds": kinds}, headers={"If-Match": etag})

    assert write.status_code == 422, write.text
    assert await _saved() is None
