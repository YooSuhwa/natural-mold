"""Company policy per tool or MCP server (tool-governance-plan 3.4, 3.10, 3.11).

The decision table is written out value by value (never one function compared with
another); the API cases act as a company member and check what is stored, what a run reads
and what the audit log says.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_policy import ToolApprovalPolicyV1
from app.approvals.tool_modes import COMPANY_FIXED, decide
from app.guardrails.pinning import _from_snapshot, _snapshot, pin_run_guardrails
from app.guardrails.policy import resolve_tool_risk, tool_policies
from app.guardrails.tool_risk import (
    ToolPolicy,
    ToolRiskPolicy,
    actions_from_json,
    tool_policy_from_json,
)
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.organization import Organization, Tenant
from app.models.resource_policy import ResourcePolicy
from app.models.tool import AgentToolLink, Tool
from app.services import audit_service
from app.tools.keys import ToolRef
from app.tools.risk import ToolRiskLevel
from tests.authz.d04_support import OTHER_USER_ID, add_server, add_tool, seed_scope
from tests.authz.d05_support import TEST_ORG_ID
from tests.authz.e09_support import MEMBER, TENANT, acting_as, actions, make_admin
from tests.authz.unit_scope_fixture import TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID, TestSession, seed_agent

POLICIES = f"{TENANT}/guardrails/tool-policies"
X, R = ToolRiskLevel.EXTERNAL_MUTATION, ToolRiskLevel.READ_ONLY
RELAXED = {"write_internal": "approval_relaxable", "external_mutation": "approval_relaxable"}
RULE = {"all": [{"source": "args", "path": "/to", "op": "ends_with", "value": "@corp.com"}]}
SERVER = "mcp_server:s1"
MAIL = ToolRef("mcp:t1", "send", SERVER)
NEVER = {"tools": {"mcp:t1": {"mode": "never"}}}
ALWAYS = {"tools": {"mcp:t1": {"mode": "always"}}}


def _policy(**raw: Any) -> ToolPolicy:
    return tool_policy_from_json(raw)


def _company(tools: dict[str, ToolPolicy], actions: dict[str, str] | None = None) -> ToolRiskPolicy:
    return ToolRiskPolicy(actions_from_json(actions or {}), frozenset(), tools)


def _decide(
    tools: dict[str, ToolPolicy],
    *,
    settings: dict[str, Any] | None = None,
    actions: dict[str, str] | None = None,
    grants: tuple[str, ...] = (),
    root: bool = True,
    level: ToolRiskLevel = X,
    path: Any = "chat",
) -> tuple[str, str]:
    found = decide(
        MAIL,
        level,
        path,
        settings=ToolApprovalPolicyV1.model_validate(settings) if settings else None,
        company=_company(tools, actions),
        grants=frozenset(grants),
        root=root,
    )
    return found.kind, found.layer


# --- the decision table -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tools", "kwargs", "expected"),
    [
        # Switched off: hidden, whatever the level or the agent says.
        (
            {"mcp:t1": {"enabled": False}},
            {"settings": NEVER, "actions": RELAXED},
            ("hidden", "company_tool_disabled"),
        ),
        ({SERVER: {"enabled": False}}, {"level": R}, ("hidden", "company_tool_disabled")),
        # The tool's own row wins over its server's.
        (
            {SERVER: {"enabled": False}, "mcp:t1": {"enabled": True}},
            {"actions": RELAXED},
            ("ask", "risk_default"),
        ),
        # Switched on inside a blocked level: usable, and it asks.
        (
            {"mcp:t1": {"enabled": True}},
            {"actions": {"external_mutation": "block"}, "settings": NEVER},
            ("ask", "company_forced"),
        ),
        ({}, {"actions": {"external_mutation": "block"}}, ("hidden", "company_block")),
        # One allowed mode fixes the tool, for the root and for a child.
        (
            {"mcp:t1": {"allowed_modes": ["always"], "default_mode": "always"}},
            {"settings": NEVER, "actions": RELAXED, "grants": ("mcp:t1",)},
            ("ask", "company_tool_locked"),
        ),
        (
            {"mcp:t1": {"allowed_modes": ["never"], "default_mode": "never"}},
            {"settings": ALWAYS},
            ("auto", "company_tool_locked"),
        ),
        (
            {SERVER: {"allowed_modes": ["never"], "default_mode": "never"}},
            {"root": False},
            ("auto", "company_tool_locked"),
        ),
        # Several allowed modes: a setting outside them is ignored and the default answers.
        (
            {"mcp:t1": {"allowed_modes": ["always", "when"], "default_mode": "always"}},
            {"settings": NEVER, "actions": RELAXED},
            ("ask", "company_tool_default"),
        ),
        (
            {"mcp:t1": {"allowed_modes": ["always", "when"], "default_mode": "always"}},
            {"grants": ("mcp:t1",), "actions": RELAXED},
            ("ask", "company_tool_default"),
        ),
        (
            {"mcp:t1": {"allowed_modes": ["always", "when"], "default_mode": "always"}},
            {"settings": {"paths": {"chat": "auto"}}, "actions": RELAXED},
            ("ask", "company_tool_default"),
        ),
        # A setting inside the allowed modes counts, even under a forced level.
        (
            {"mcp:t1": {"allowed_modes": ["always", "never"], "default_mode": "always"}},
            {"settings": NEVER},
            ("auto", "tool_never"),
        ),
        (
            {"mcp:t1": {"allowed_modes": ["always", "never"], "default_mode": "always"}},
            {"settings": NEVER, "root": False},
            ("ask", "company_tool_default"),
        ),
        # A default alone: it answers where nothing closer does, and never under a forced level.
        (
            {"mcp:t1": {"default_mode": "never"}},
            {"actions": RELAXED},
            ("auto", "company_tool_default"),
        ),
        ({"mcp:t1": {"default_mode": "never"}}, {}, ("ask", "company_forced")),
        (
            {"mcp:t1": {"default_mode": "never"}},
            {"actions": RELAXED, "settings": ALWAYS},
            ("ask", "tool_always"),
        ),
        ({"mcp:t1": {"default_mode": "always"}}, {"level": R}, ("ask", "company_tool_default")),
        (
            {SERVER: {"default_mode": "always"}, "mcp:t1": {"default_mode": "never"}},
            {"actions": RELAXED},
            ("auto", "company_tool_default"),
        ),
    ],
)
def test_the_company_tool_layers(
    tools: dict[str, dict[str, Any]], kwargs: dict[str, Any], expected: tuple[str, str]
) -> None:
    assert _decide({key: _policy(**raw) for key, raw in tools.items()}, **kwargs) == expected


def test_a_company_when_asks_by_its_rule_and_stays_beside_an_agents_rule() -> None:
    company = {"mcp:t1": _policy(allowed_modes=["always", "when"], default_mode="when", rule=RULE)}
    own = {"all": [{"source": "args", "path": "/cc", "op": "exists"}]}

    plain = decide(MAIL, X, "chat", settings=None, company=_company(company))
    agent = decide(
        MAIL,
        X,
        "chat",
        settings=ToolApprovalPolicyV1.model_validate(
            {"tools": {"mcp:t1": {"mode": "when", "rule": own}}}
        ),
        company=_company(company),
    )

    assert (plain.kind, plain.layer) == ("when", "company_tool_default")
    assert plain.rule is not None and plain.rule.model_dump(mode="json", exclude_none=True) == RULE
    assert (agent.kind, agent.layer) == ("when", "tool_when")
    assert agent.rule is not None and (agent.rule.all or [])[0].path == "/cc"
    assert agent.also is not None and (agent.also.all or [])[0].path == "/to"


def test_locked_and_disabled_are_layers_nothing_below_changes() -> None:
    assert {"company_tool_disabled", "company_tool_locked"} <= COMPANY_FIXED
    assert "company_tool_default" not in COMPANY_FIXED


def test_the_floor_every_agent_gets() -> None:
    def asks(**raw: Any) -> bool:
        return _company({"mcp:t1": _policy(**raw)}).tool_asks(MAIL)

    assert asks(allowed_modes=["always", "when"], default_mode="always")
    assert asks(default_mode="always")
    assert asks(default_mode="when", rule=RULE)
    assert not asks(default_mode="never")
    assert not asks(allowed_modes=["always", "never"], default_mode="never")
    assert not asks(enabled=True)
    assert not _company({}).tool_asks(MAIL)


@pytest.mark.parametrize(
    "raw",
    [
        {"enabled": "no"},
        {"allowed_modes": []},
        {"allowed_modes": ["sometimes"]},
        {"default_mode": "maybe"},
        {"default_mode": "never", "allowed_modes": ["always"]},
        {"rule": "x"},
        [],
    ],
)
def test_a_stored_policy_of_another_shape_is_refused(raw: Any) -> None:
    from app.guardrails.tool_risk import InvalidToolRiskError

    with pytest.raises(InvalidToolRiskError):
        tool_policy_from_json(raw)


# --- what a run reads ---------------------------------------------------------------------


async def _row(db: AsyncSession, key: str, **values: Any) -> None:
    db.add(ResourcePolicy(tenant_id=TEST_TENANT_ID, org_id=None, resource_key=key, **values))
    await db.commit()


async def test_a_run_reads_company_rows_and_an_unreadable_row_is_off(db: AsyncSession) -> None:
    await seed_scope(db)
    await _row(db, "mcp:a", default_mode="always", allowed_modes=["always", "when"])
    await _row(db, "mcp:bad", default_mode="sometimes")
    db.add(
        ResourcePolicy(
            tenant_id=TEST_TENANT_ID, org_id=TEST_ORG_ID, resource_key="mcp:org", enabled=False
        )
    )
    await db.commit()

    async with TestSession() as check:
        found = await tool_policies(check, TEST_TENANT_ID)
        policy = await resolve_tool_risk(check, TEST_TENANT_ID, "standard")
        builder = await resolve_tool_risk(check, TEST_TENANT_ID, "builder")

    assert found == {
        "mcp:a": ToolPolicy(None, "always", frozenset({"always", "when"}), None),
        "mcp:bad": ToolPolicy(False, None, None, None),
    }
    assert policy is not None and policy.blocks(ToolRef("mcp:bad", "x"), R)
    assert not policy.blocks(ToolRef("mcp:org", "x"), R)  # organization rows are not read yet
    assert builder is None


def test_a_snapshot_keeps_the_tool_policies_and_an_unreadable_one_is_off() -> None:
    policy = _company({"mcp:t1": _policy(allowed_modes=["always"], default_mode="always")})

    record = _snapshot(None, policy)
    pinned = _from_snapshot(record).tool_risk
    broken = _from_snapshot(
        {**record, "tool_policies": {"mcp:t1": {"default_mode": "sometimes"}}}
    ).tool_risk

    assert record["tool_policies"] == {
        "mcp:t1": {
            "enabled": None,
            "default_mode": "always",
            "allowed_modes": ["always"],
            "rule": None,
        }
    }
    assert pinned is not None and pinned.tools == policy.tools
    assert broken is not None and broken.tools == {"mcp:t1": ToolPolicy(False, None, None, None)}
    assert "tool_policies" not in _snapshot(None, _company({}, {"external_mutation": "block"}))


async def _run(db: AsyncSession, conversation_id: uuid.UUID, agent_id: uuid.UUID) -> uuid.UUID:
    run = ConversationRun(
        conversation_id=conversation_id,
        agent_id=agent_id,
        user_id=TEST_USER_ID,
        source="chat",
        status="running",
        is_active=False,
    )
    db.add(run)
    await db.commit()
    return run.id


async def _pin(
    run_id: uuid.UUID, conversation_id: uuid.UUID, agent: uuid.UUID, resume: bool
) -> Any:
    async with TestSession() as db:
        pinned = await pin_run_guardrails(
            db,
            run_id=run_id,
            conversation_id=conversation_id,
            context=unit_context(TEST_USER_ID),
            agent_id=agent,
            runtime_profile="standard",
            resume=resume,
        )
        return pinned.tool_risk


async def test_a_resume_keeps_the_pinned_policy_but_a_tool_switched_off_since_is_off(
    db: AsyncSession,
) -> None:
    await seed_scope(db)
    _, _, agent = await seed_agent(db)
    conversation = Conversation(
        agent_id=agent.id,
        title="pin",
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        agent_name_snapshot="Test Agent",
        last_activity_at=datetime.now(UTC),
    )
    db.add(conversation)
    await db.flush()
    conversation_id, agent_id = conversation.id, agent.id
    await _row(db, "mcp:kept", default_mode="always", allowed_modes=["always"])
    first = await _pin(await _run(db, conversation_id, agent_id), conversation_id, agent_id, False)

    async with TestSession() as session:
        kept = await session.scalar(
            select(ResourcePolicy).where(ResourcePolicy.resource_key == "mcp:kept")
        )
        assert kept is not None
        kept.allowed_modes, kept.default_mode = None, "never"  # relaxed after the run began
        session.add(
            ResourcePolicy(
                tenant_id=TEST_TENANT_ID, org_id=None, resource_key="mcp:off", enabled=False
            )
        )
        await session.commit()
    resumed = await _pin(await _run(db, conversation_id, agent_id), conversation_id, agent_id, True)
    next_turn = await _pin(
        await _run(db, conversation_id, agent_id), conversation_id, agent_id, False
    )

    locked = ToolPolicy(None, "always", frozenset({"always"}), None)
    off = ToolPolicy(False, None, None, None)
    assert first is not None and first.tools == {"mcp:kept": locked}
    assert resumed is not None and resumed.tools == {"mcp:kept": locked, "mcp:off": off}
    assert next_turn is not None
    assert next_turn.tools == {"mcp:kept": ToolPolicy(None, "never", None, None), "mcp:off": off}


# --- the company API ----------------------------------------------------------------------


@pytest.fixture
async def _member(db: AsyncSession, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch):
    # A refused write is audited in a session of its own; keep it in the test database.
    monkeypatch.setattr(audit_service, "async_session", TestSession)
    await seed_scope(db)
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        yield


async def _stored(key: str) -> ResourcePolicy | None:
    async with TestSession() as check:
        return await check.scalar(select(ResourcePolicy).where(ResourcePolicy.resource_key == key))


@pytest.mark.usefixtures("_member")
async def test_an_admin_saves_changes_and_removes_a_policy_and_each_step_is_audited(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    _, tool = await add_server(db, name="Mail")
    key = f"mcp:{tool.id}"

    saved = await client.put(f"{POLICIES}/{key}", json={"allowed_modes": ["always", "always"]})
    again = await client.put(f"{POLICIES}/{key}", json={"allowed_modes": ["always"]})
    changed = await client.put(
        f"{POLICIES}/{key}",
        json={
            "enabled": False,
            "allowed_modes": ["always", "when"],
            "default_mode": "when",
            "rule": RULE,
        },
    )
    listed = await client.get(POLICIES)
    removed = await client.put(f"{POLICIES}/{key}", json={})

    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["warnings"] == []
    assert {k: body["policy"][k] for k in ("resource_key", "resource_name", "enabled")} == {
        "resource_key": key,
        "resource_name": "Mail-tool",
        "enabled": None,
    }
    # One allowed mode is the default too: the tool is fixed to it.
    assert (body["policy"]["allowed_modes"], body["policy"]["default_mode"]) == (
        ["always"],
        "always",
    )
    assert changed.status_code == 200, changed.text
    [row] = listed.json()["policies"]
    assert (row["enabled"], row["default_mode"], row["allowed_modes"]) == (
        False,
        "when",
        ["always", "when"],
    )
    assert row["rule"]["all"][0]["path"] == "/to"
    assert removed.json() == {"policy": None, "warnings": []}
    assert await _stored(key) is None
    assert (await client.get(POLICIES)).json() == {"policies": []}

    events = [e for e in await actions(key) if e.action == "resource_policy.updated"]
    assert again.status_code == 200 and len(events) == 3  # the unchanged save wrote nothing
    create, update, delete = (e.event_metadata or {} for e in events)
    locked = {"enabled": None, "default_mode": "always", "allowed_modes": ["always"], "rule": None}
    assert (create["before"], create["after"]) == (None, {**locked, "warnings": []})
    assert update["before"] == locked
    assert update["after"]["enabled"] is False and update["after"]["default_mode"] == "when"
    assert delete["before"]["enabled"] is False and delete["after"] is None
    assert {(e.target_type, e.target_name_snapshot, e.outcome) for e in events} == {
        ("resource_policy", "Mail-tool", "success")
    }
    assert {e.actor_user_id for e in events} == {TEST_USER_ID}


@pytest.mark.usefixtures("_member")
async def test_a_saved_policy_is_what_the_next_run_reads(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    server, tool = await add_server(db, name="Mail")
    server_ref = f"mcp_server:{server.id}"
    ref = ToolRef(f"mcp:{tool.id}", "Mail-tool", server_ref)

    off = await client.put(f"{POLICIES}/{server_ref}", json={"enabled": False})
    async with TestSession() as check:
        closed = await resolve_tool_risk(check, TEST_TENANT_ID, "standard")
    on = await client.put(
        f"{POLICIES}/{ref.key}", json={"enabled": True, "allowed_modes": ["never"]}
    )
    async with TestSession() as check:
        opened = await resolve_tool_risk(check, TEST_TENANT_ID, "standard")

    assert (off.status_code, on.status_code) == (200, 200)
    assert closed is not None and opened is not None
    hidden = decide(ref, X, "chat", settings=None, company=closed)
    runs = decide(ref, X, "trigger", settings=None, company=opened, root=False)
    assert (hidden.kind, hidden.layer) == ("hidden", "company_tool_disabled")
    assert (runs.kind, runs.layer) == ("auto", "company_tool_locked")


@pytest.mark.usefixtures("_member")
@pytest.mark.parametrize(
    "body",
    [
        {"allowed_modes": ["always", "never"]},  # several modes and no default
        {"allowed_modes": ["always"], "default_mode": "never"},
        {"allowed_modes": []},
        {"allowed_modes": ["sometimes"]},
        {"default_mode": "when"},  # a when without its rule
        {"default_mode": "always", "rule": RULE},
        {
            "default_mode": "when",
            "rule": {"all": [{"source": "result", "path": "/x", "op": "exists"}]},
        },
        {"enabled": "maybe"},
    ],
)
async def test_a_policy_a_run_would_refuse_is_not_saved(
    client: AsyncClient, db: AsyncSession, body: dict[str, Any]
) -> None:
    await make_admin(db)
    _, tool = await add_server(db)
    key = f"mcp:{tool.id}"
    await client.put(f"{POLICIES}/{key}", json={"default_mode": "always"})

    response = await client.put(f"{POLICIES}/{key}", json=body)

    assert response.status_code == 422, response.text
    row = await _stored(key)
    assert row is not None and (row.default_mode, row.allowed_modes) == ("always", None)
    assert len([e for e in await actions(key) if e.action == "resource_policy.updated"]) == 1


async def _foreign_server(db: AsyncSession) -> tuple[McpServer, McpTool, Tool]:
    tenant = Tenant(id=uuid.uuid4(), name="Other", slug="other-co", secrets_namespace="tenants/o")
    db.add(tenant)
    await db.flush()
    org = Organization(id=uuid.uuid4(), tenant_id=tenant.id, name="Other org", slug="other-org")
    db.add(org)
    await db.flush()
    server = McpServer(
        user_id=OTHER_USER_ID,
        org_id=org.id,
        tenant_id=tenant.id,
        name="Theirs",
        transport="streamable_http",
        url="https://mcp.other.test",
    )
    tool = Tool(
        user_id=OTHER_USER_ID,
        org_id=org.id,
        tenant_id=tenant.id,
        definition_key="http_request",
        name="theirs",
        parameters={},
    )
    db.add_all([server, tool])
    await db.flush()
    mcp_tool = McpTool(server_id=server.id, name="send", input_schema={})
    db.add(mcp_tool)
    await db.commit()
    return server, mcp_tool, tool


@pytest.mark.usefixtures("_member")
async def test_another_companys_tool_is_not_found_like_a_typo(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    server, mcp_tool, tool = await _foreign_server(db)
    keys = [
        f"mcp_server:{server.id}",
        f"mcp:{mcp_tool.id}",
        f"tool:{tool.id}",
        f"mcp:{uuid.uuid4()}",
        "mcp:not-a-uuid",
        "builtin:no_such_tool",
        "skill:x",
    ]

    for key in keys:
        saved = await client.put(f"{POLICIES}/{key}", json={"enabled": False})
        preview = await client.post(f"{POLICIES}/{key}/impact", json={"enabled": False})
        assert (saved.status_code, preview.status_code) == (404, 404), key
        assert saved.json()["error"]["code"] == "TOOL_POLICY_TARGET_NOT_FOUND"
        assert await _stored(key) is None
    names = {item["name"] for item in (await client.get(f"{POLICIES}/targets")).json()}
    assert not names & {"Theirs", "theirs"}


@pytest.mark.usefixtures("_member")
async def test_a_member_reads_the_policies_and_only_an_admin_changes_them(
    client: AsyncClient, db: AsyncSession
) -> None:
    _, tool = await add_server(db)
    key = f"mcp:{tool.id}"
    await _row(db, key, resource_name="srv-tool", enabled=False)

    listed = await client.get(POLICIES)
    targets = await client.get(f"{POLICIES}/targets")
    saved = await client.put(f"{POLICIES}/{key}", json={})
    preview = await client.post(f"{POLICIES}/{key}/impact", json={"enabled": False})

    assert listed.status_code == 200
    assert [p["resource_key"] for p in listed.json()["policies"]] == [key]
    assert (targets.status_code, saved.status_code, preview.status_code) == (403, 403, 403)
    row = await _stored(key)
    assert row is not None and row.enabled is False
    assert await actions(key) == []


@pytest.mark.usefixtures("_member")
async def test_a_risky_tool_set_to_never_is_saved_with_a_warning_in_the_audit(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    tool = await add_tool(db, name="send_mail")
    key = f"tool:{tool.id}"

    risky = await client.put(f"{POLICIES}/{key}", json={"allowed_modes": ["never"]})
    read_only = await client.put(
        f"{POLICIES}/builtin:current_datetime", json={"default_mode": "never"}
    )
    asking = await client.put(f"{POLICIES}/builtin:execute", json={"default_mode": "always"})
    off = await client.put(
        f"{POLICIES}/builtin:write_file", json={"enabled": False, "default_mode": "never"}
    )

    assert risky.status_code == 200, risky.text
    assert risky.json()["warnings"] == [
        {"code": "risky_without_asking", "risk_level": "external_mutation"}
    ]
    assert risky.json()["policy"]["default_mode"] == "never"
    assert [r.json()["warnings"] for r in (read_only, asking, off)] == [[], [], []]
    [event] = [e for e in await actions(key) if e.action == "resource_policy.updated"]
    assert event.event_metadata is not None
    assert event.event_metadata["after"]["warnings"] == ["risky_without_asking"]


@pytest.mark.usefixtures("_member")
async def test_the_targets_are_this_companys_tools_servers_and_the_built_ins(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    server, mcp_tool = await add_server(db, name="Mail")
    tool = await add_tool(db, name="hook")

    found = {item["key"]: item for item in (await client.get(f"{POLICIES}/targets")).json()}

    assert found[f"mcp_server:{server.id}"] == {
        "key": f"mcp_server:{server.id}",
        "name": "Mail",
        "kind": "mcp_server",
        "server_key": None,
        "source": None,
        "risk_level": None,
    }
    assert found[f"mcp:{mcp_tool.id}"] == {
        "key": f"mcp:{mcp_tool.id}",
        "name": "Mail-tool",
        "kind": "mcp_tool",
        "server_key": f"mcp_server:{server.id}",
        "source": "Mail",
        "risk_level": "external_mutation",
    }
    assert (found[f"tool:{tool.id}"]["kind"], found[f"tool:{tool.id}"]["risk_level"]) == (
        "tool",
        "external_mutation",
    )
    assert found["builtin:execute"]["risk_level"] == "code_execution"
    assert found["builtin:current_datetime"]["risk_level"] == "read_only"
    assert "builtin:execute_in_skill" in found


@pytest.mark.usefixtures("_member")
async def test_the_impact_preview_lists_the_agent_settings_that_stop_applying(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    server, tool = await add_server(db, name="Mail")
    other = McpTool(server_id=server.id, name="read", input_schema={})
    db.add(other)
    await db.flush()
    key, other_key = f"mcp:{tool.id}", f"mcp:{other.id}"
    _, _, by_key = await seed_agent(db, name="B keyed")
    by_key.runtime_policy = {
        "version": 1,
        "tool_approval": {"tools": {key: {"mode": "never"}, other_key: {"mode": "always"}}},
    }
    _, _, by_name = await seed_agent(db, name="A named")
    by_name.runtime_policy = {
        "version": 1,
        "tool_approval": {"tools": {"Mail-tool": {"mode": "always"}}},
    }
    _, _, plain = await seed_agent(db, name="C plain")
    await db.commit()

    narrowed = await client.post(f"{POLICIES}/{key}/impact", json={"allowed_modes": ["always"]})
    off = await client.post(f"{POLICIES}/mcp_server:{server.id}/impact", json={"enabled": False})
    await client.put(f"{POLICIES}/{other_key}", json={"allowed_modes": ["always"]})
    own_row = await client.post(
        f"{POLICIES}/mcp_server:{server.id}/impact", json={"enabled": False}
    )
    harmless = await client.post(f"{POLICIES}/{key}/impact", json={"default_mode": "always"})

    assert narrowed.status_code == 200, narrowed.text
    assert narrowed.json() == {
        "agents": [
            {
                "id": str(by_key.id),
                "name": "B keyed",
                "tool_key": key,
                "tool_name": "Mail-tool",
                "mode": "never",
                "reason": "mode_not_allowed",
            }
        ],
        "total": 1,
        "warnings": [],
    }
    assert [(a["name"], a["tool_name"], a["reason"]) for a in off.json()["agents"]] == [
        ("A named", "Mail-tool", "disabled"),
        ("B keyed", "Mail-tool", "disabled"),
        ("B keyed", "read", "disabled"),
    ]
    # A tool with its own restriction keeps it: the server's change does not reach it.
    assert [(a["name"], a["tool_name"]) for a in own_row.json()["agents"]] == [
        ("A named", "Mail-tool"),
        ("B keyed", "Mail-tool"),
    ]
    assert harmless.json()["agents"] == [] and harmless.json()["total"] == 0
    assert await _stored(key) is None  # a preview saves nothing
    assert str(plain.id) not in narrowed.text


async def test_the_agent_screen_gets_the_modes_the_company_allows(
    client: AsyncClient, db: AsyncSession
) -> None:
    _, _, agent = await seed_agent(db)
    limited = await add_tool(db, name="send_mail")
    free = await add_tool(db, name="read_mail")
    for tool in (limited, free):
        db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))
    await _row(db, f"tool:{limited.id}", allowed_modes=["when", "always"])
    await db.commit()

    view = (await client.get(f"/api/agents/{agent.id}/tool-approval")).json()

    allowed = {tool["name"]: tool["allowed_modes"] for tool in view["tools"]}
    assert allowed["send_mail"] == ["always", "when"]
    assert allowed["read_mail"] is None
