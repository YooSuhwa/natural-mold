"""Tool approval modes (approval-modes-plan): the layer table, assembly, grants and the API.

Expected values are written out (never one function compared with another): the effective
table and the run assembly both call ``tool_modes.decide``, so each is pinned on its own.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_policy import (
    RuntimePolicyV1,
    ToolApprovalPolicyV1,
    resolve_runtime_policy,
)
from app.approvals import tool_modes
from app.approvals.tool_modes import Decision, ToolApprovalPlan, decide, grantable
from app.conditions import Clause, Condition, clause_holds
from app.guardrails.tool_risk import ToolRiskPolicy, actions_from_json, policy_from_actions
from app.models.agent import Agent
from app.models.audit_event import AuditEvent
from app.models.conversation import Conversation
from app.models.conversation_tool_grant import ConversationToolGrant
from app.models.tool import AgentToolLink, Tool
from app.tools.risk import ToolRiskLevel
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, seed_agent

W, X, R = ToolRiskLevel.WRITE_INTERNAL, ToolRiskLevel.EXTERNAL_MUTATION, ToolRiskLevel.READ_ONLY
RULE = {"all": [{"source": "args", "path": "/to", "op": "ends_with", "value": "@corp.com"}]}


def _settings(**raw: Any) -> ToolApprovalPolicyV1:
    return ToolApprovalPolicyV1.model_validate(raw)


def _company(
    actions: dict[str, str] | None = None, always: frozenset[str] = frozenset()
) -> ToolRiskPolicy:
    return ToolRiskPolicy(actions_from_json(actions or {}), always)


RELAXED = {"write_internal": "approval_relaxable", "external_mutation": "approval_relaxable"}


@pytest.mark.parametrize(
    ("name", "level", "path", "settings", "company", "grants", "expected"),
    [
        # Defaults: today's behaviour (the company default forces risky levels).
        ("mail", X, "chat", None, None, (), ("ask", "company_forced")),
        ("search", R, "chat", None, None, (), ("auto", "risk_default")),
        # Layer 1: company block hides, whatever the agent says.
        (
            "mail",
            X,
            "chat",
            {"tools": {"mail": {"mode": "never"}}},
            {"external_mutation": "block"},
            (),
            ("hidden", "company_block"),
        ),
        # Layer 2: always-ask list and a forced level beat every agent setting.
        (
            "mail",
            X,
            "chat",
            {"tools": {"mail": {"mode": "never"}}},
            ("always", RELAXED),
            (),
            ("ask", "company_always"),
        ),
        (
            "mail",
            X,
            "chat",
            {"tools": {"mail": {"mode": "never"}}, "paths": {"chat": "auto"}},
            None,
            ("mail",),
            ("ask", "company_forced"),
        ),
        # Layer 3: per tool, under a relaxable cap.
        (
            "mail",
            X,
            "chat",
            {"tools": {"mail": {"mode": "never"}}},
            RELAXED,
            (),
            ("auto", "tool_never"),
        ),
        (
            "mail",
            X,
            "chat",
            {"tools": {"mail": {"mode": "when", "rule": RULE}}},
            RELAXED,
            (),
            ("when", "tool_when"),
        ),
        (
            "search",
            R,
            "chat",
            {"tools": {"search": {"mode": "always"}}},
            None,
            (),
            ("ask", "tool_always"),
        ),
        (
            "mail",
            X,
            "chat",
            {"tools": {"mail": {"mode": "always"}}, "paths": {"chat": "auto"}},
            RELAXED,
            ("mail",),
            ("ask", "tool_always"),
        ),
        # Layer 4: a conversation grant, chat only.
        ("mail", X, "chat", None, RELAXED, ("mail",), ("auto", "conversation_grant")),
        ("mail", X, "trigger", None, RELAXED, ("mail",), ("ask", "risk_default")),
        # Layer 5: the path's mode.
        (
            "mail",
            X,
            "workflow",
            {"paths": {"workflow": "auto"}},
            RELAXED,
            (),
            ("auto", "agent_mode"),
        ),
        ("mail", X, "chat", {"paths": {"workflow": "auto"}}, RELAXED, (), ("ask", "risk_default")),
        # A level the company allows runs without asking by default.
        ("mail", X, "chat", None, {"external_mutation": "allow"}, (), ("auto", "risk_default")),
    ],
)
def test_the_layer_table(
    name: str,
    level: ToolRiskLevel,
    path: str,
    settings: dict[str, Any] | None,
    company: Any,
    grants: tuple[str, ...],
    expected: tuple[str, str],
) -> None:
    if isinstance(company, tuple):  # ("always", actions): the tool is always asked
        policy: ToolRiskPolicy | None = _company(company[1], frozenset({name}))
    else:
        policy = _company(company) if company else None
    decision = decide(
        name,
        level,
        path,  # type: ignore[arg-type]
        settings=_settings(**settings) if settings else None,
        company=policy,
        grants=frozenset(grants),
    )
    assert (decision.kind, decision.layer) == expected


def test_an_ask_set_through_the_old_middleware_still_asks() -> None:
    decision = decide(
        "search", R, "chat", settings=None, company=None, legacy_always=frozenset({"search"})
    )
    assert decision == Decision("ask", "tool_always")


def test_what_a_conversation_may_allow() -> None:
    assert grantable(
        "note", W, settings=None, company=_company({"write_internal": "approval_relaxable"})
    )
    assert not grantable("mail", X, settings=None, company=None)  # forced by default
    assert grantable("mail", X, settings=None, company=_company(RELAXED))
    always = _settings(tools={"mail": {"mode": "always"}})
    assert not grantable("mail", X, settings=always, company=_company(RELAXED))
    assert not grantable("search", R, settings=None, company=None)  # nothing to lift


def test_rules_read_args_only_and_new_text_ops_hold() -> None:
    with pytest.raises(ValueError, match="args only"):
        ToolApprovalPolicyV1.model_validate(
            {
                "tools": {
                    "mail": {"mode": "when", "rule": {"all": [{"source": "input", "op": "exists"}]}}
                }
            }
        )
    with pytest.raises(ValueError, match="has a rule"):
        ToolApprovalPolicyV1.model_validate({"tools": {"mail": {"mode": "when"}}})
    ends = Clause(source="args", path="/to", op="ends_with", value="@corp.com")
    starts = Clause(source="args", path="/to", op="starts_with", value="kim")
    values = {"args": {"to": "kim@corp.com"}}
    assert clause_holds(ends, values) and clause_holds(starts, values)
    assert not clause_holds(ends, {"args": {"to": 3}})


def test_a_policy_without_tool_approval_keeps_its_hash() -> None:
    plain = resolve_runtime_policy({"version": 1})
    empty = resolve_runtime_policy({"version": 1, "tool_approval": {}})
    assert plain.policy_hash == empty.policy_hash
    set_ = RuntimePolicyV1.model_validate(
        {"version": 1, "tool_approval": {"paths": {"chat": "auto"}}}
    )
    assert resolve_runtime_policy(set_.model_dump(mode="json")).policy_hash != plain.policy_hash


class _Tool:
    def __init__(self, name: str, level: ToolRiskLevel) -> None:
        self.name = name
        self.metadata = {"moldy_risk": {"risk_level": level.value}}


def test_apply_lifts_conditions_and_records_what_runs_unasked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    levels = {"mail": X, "note": W, "search": R}
    monkeypatch.setattr(
        tool_modes,
        "level_of",
        lambda tool: levels.get(tool if isinstance(tool, str) else tool.name, W),
    )
    plan = ToolApprovalPlan(
        "chat",
        settings=_settings(
            tools={"mail": {"mode": "when", "rule": RULE}, "search": {"mode": "always"}},
            paths={"chat": "auto"},
        ),
        company=_company(RELAXED),
    )
    interrupt_on = {
        "mail": {"allowed_decisions": ["approve", "reject"]},
        "note": {"allowed_decisions": ["approve", "reject"]},
        "ask_user": {"allowed_decisions": ["respond"]},
    }
    tools = [_Tool(name, level) for name, level in levels.items()]
    applied = tool_modes.apply(interrupt_on, tools, plan)  # type: ignore[arg-type]
    result = applied.interrupt_on or {}
    assert "note" not in result  # mode auto
    assert result["ask_user"] == {"allowed_decisions": ["respond"]}  # untouched
    assert "search" in result and "도구 설정: 항상 묻기" in result["search"]["description"](
        {"name": "search", "args": {}}, None, None
    )
    when = result["mail"]["when"]
    assert when(SimpleNamespace(tool_call={"args": {"to": "lee@corp.com"}})) is True
    assert when(SimpleNamespace(tool_call={"args": {"to": "lee@other.com"}})) is False
    assert when(SimpleNamespace(tool_call={"args": None})) is False
    # Deep Agents' own file tools are write level too: the same mode lifts them.
    assert applied.auto_allowed == {
        "note": "agent_mode",
        "write_file": "agent_mode",
        "edit_file": "agent_mode",
        "execute": "agent_mode",
    }


def test_runs_with_relaxing_settings_take_the_ordinary_path() -> None:
    from app.agent_runtime.prepared_run_input_policy import is_legacy_fallback

    assert is_legacy_fallback("tool_approval")
    assert is_legacy_fallback("child:helper:tool_approval")
    trivial = ToolApprovalPlan("chat", settings=_settings())
    assert not trivial.relaxes
    assert ToolApprovalPlan("chat", grants=frozenset({"mail"})).relaxes
    assert not ToolApprovalPlan("trigger", grants=frozenset({"mail"})).relaxes


def test_company_policy_reads_relaxable_and_always_ask() -> None:
    policy = policy_from_actions({"external_mutation": "approval_relaxable"}, frozenset({"mail"}))
    assert policy is not None
    assert (policy.action("external_mutation"), policy.always_ask) == (
        "approval_relaxable",
        frozenset({"mail"}),
    )
    assert policy_from_actions(None, frozenset({"mail"})) is not None
    assert policy_from_actions(None) is None


# --- the API --------------------------------------------------------------------------------


async def _agent_with_mail(db: AsyncSession, settings: dict[str, Any] | None = None) -> Agent:
    _, _, agent = await seed_agent(db)
    tool = Tool(
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        definition_key="http_request",
        name="send_mail",
        parameters={},
    )
    db.add(tool)
    await db.flush()
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))
    if settings is not None:
        agent.runtime_policy = {"version": 1, "tool_approval": settings}
    await db.commit()
    return agent


@pytest.mark.asyncio
async def test_the_effective_table_shows_each_path(client: AsyncClient, db: AsyncSession) -> None:
    agent = await _agent_with_mail(db, {"paths": {"workflow": "auto"}})
    view = (await client.get(f"/api/agents/{agent.id}/tool-approval")).json()
    mail = next(tool for tool in view["tools"] if tool["name"] == "send_mail")
    assert mail["level"] == "external_mutation"
    cells = {
        cell["path"]: (cell["kind"], cell["layer"], cell["delivery"]) for cell in mail["cells"]
    }
    # The company keeps external mutations forced (its default): the workflow "auto" cannot lift it.
    assert cells == {
        "chat": ("ask", "company_forced", "card"),
        "trigger": ("ask", "company_forced", "per_schedule"),
        "workflow": ("ask", "company_forced", "wait"),
        "api": ("ask", "company_forced", "per_deployment"),
    }
    assert mail["locked"] is True and mail["grantable"] is False
    assert view["settings"]["paths"]["workflow"] == "auto"


async def _conversation(db: AsyncSession, agent: Agent) -> Conversation:
    from app.services import chat_service
    from tests.authz.unit_scope_fixture import unit_context

    conversation = await chat_service.create_conversation(
        db, agent.id, "t", context=unit_context(TEST_USER_ID)
    )
    await db.commit()
    return conversation


@pytest.mark.asyncio
async def test_a_grant_needs_a_lifted_cap_and_is_audited(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.routers import tool_approval as route

    agent = await _agent_with_mail(db)
    conversation = await _conversation(db, agent)
    url = f"/api/conversations/{conversation.id}/tool-grants"
    refused = await client.post(url, json={"tool": "send_mail"})
    assert (refused.status_code, refused.json()["error"]["code"]) == (422, "TOOL_GRANT_NOT_ALLOWED")

    async def relaxed(*_: Any) -> ToolRiskPolicy:
        return _company(RELAXED)

    monkeypatch.setattr(route, "resolve_tool_risk", relaxed)
    made = await client.post(url, json={"tool": "send_mail"})
    assert made.status_code == 201, made.text
    again = await client.post(url, json={"tool": "send_mail"})
    assert again.json()["id"] == made.json()["id"]  # one live grant per tool
    assert [g["tool_name"] for g in (await client.get(url)).json()] == ["send_mail"]
    unknown = await client.post(url, json={"tool": "not_a_tool"})
    assert unknown.status_code == 422
    revoked = await client.delete(f"{url}/send_mail")
    assert revoked.status_code == 204
    assert (await client.get(url)).json() == []
    actions = (
        (
            await db.execute(
                sa.select(AuditEvent.action).where(AuditEvent.target_id == str(conversation.id))
            )
        )
        .scalars()
        .all()
    )
    assert sorted(actions) == ["approval.grant_created", "approval.grant_revoked"]


@pytest.mark.asyncio
async def test_another_persons_conversation_is_404(client: AsyncClient, db: AsyncSession) -> None:
    agent = await _agent_with_mail(db)
    conversation = await _conversation(db, agent)
    conversation.user_id = uuid.uuid4()
    from app.models.user import User

    db.add(User(id=conversation.user_id, email=f"{conversation.user_id}@t.io", name="x"))
    await db.commit()
    url = f"/api/conversations/{conversation.id}/tool-grants"
    assert (await client.get(url)).status_code == 404
    assert (await client.post(url, json={"tool": "send_mail"})).status_code == 404


@pytest.mark.asyncio
async def test_changing_the_settings_revokes_the_grants(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent = await _agent_with_mail(db)
    conversation = await _conversation(db, agent)
    db.add(
        ConversationToolGrant(
            conversation_id=conversation.id, tool_name="send_mail", granted_by=TEST_USER_ID
        )
    )
    await db.commit()
    detail = (await client.get(f"/api/agents/{agent.id}")).json()
    policy = {**detail["runtime_policy_effective"], "tool_approval": {"paths": {"chat": "auto"}}}
    saved = await client.put(f"/api/agents/{agent.id}", json={"runtime_policy": policy})
    assert saved.status_code == 200, saved.text
    row = await db.scalar(sa.select(ConversationToolGrant))
    assert row is not None
    await db.refresh(row)
    assert (row.revoked_at is not None, row.revoked_reason) == (True, "settings_changed")
    audit = await db.scalar(
        sa.select(AuditEvent).where(AuditEvent.action == "agent.tool_approval_updated")
    )
    assert audit is not None and audit.event_metadata is not None
    assert audit.event_metadata["grants_revoked"] == 1
    assert audit.event_metadata["before"] is None and audit.event_metadata["after"]


@pytest.mark.asyncio
async def test_a_risky_call_that_ran_unasked_is_audited(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[dict[str, Any]] = []

    async def fake_record(_db: Any, **kwargs: Any) -> None:
        recorded.append(kwargs)

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def commit(self) -> None:
            return None

    monkeypatch.setattr("app.services.audit_service.record_event", fake_record)
    monkeypatch.setattr("app.database.tenant_session", lambda *_a, **_k: _Session())
    target = tool_modes.AuditTarget(
        TEST_TENANT_ID, TEST_ORG_ID, TEST_USER_ID, "agent-1", "thread-1"
    )
    middleware = tool_modes.AutoAllowedAuditMiddleware({"note": "agent_mode"}, target)

    async def handler(_request: Any) -> str:
        return "ran"

    ran = await middleware.awrap_tool_call(SimpleNamespace(tool_call={"name": "note"}), handler)
    skipped = await middleware.awrap_tool_call(
        SimpleNamespace(tool_call={"name": "other"}), handler
    )
    assert (ran, skipped) == ("ran", "ran")
    assert [(r["action"], r["reason_code"], r["metadata"]["tool"]) for r in recorded] == [
        ("approval.auto_allowed", "agent_mode", "note")
    ]


def test_condition_model_is_the_workflow_one() -> None:
    from app.workflows.definition import Condition as WorkflowCondition

    assert WorkflowCondition is Condition


def test_a_run_pins_the_always_ask_list_with_the_actions() -> None:
    from app.guardrails.pinning import _from_snapshot, _snapshot

    policy = _company({"external_mutation": "approval_relaxable"}, frozenset({"send_mail"}))
    pinned = _from_snapshot(_snapshot(None, policy))
    assert pinned.tool_risk is not None
    assert pinned.tool_risk.always_ask == frozenset({"send_mail"})
    assert pinned.tool_risk.action("external_mutation") == "approval_relaxable"
    plain = _snapshot(None, _company({"external_mutation": "block"}))
    assert "always_ask_tools" not in plain  # earlier pins keep their shape
