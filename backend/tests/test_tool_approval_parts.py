"""Skills and child agents under the same approval rules as tools (tool-governance-plan 3.8).

A skill is a part of ``execute_in_skill`` (``skill:<id>``), a child agent a part of ``task``
(``agent:<id>``); which one a call means is read from its arguments. Every expected answer is
written out value by value.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_policy import ToolApprovalPolicyV1
from app.approvals import tool_modes
from app.approvals.tool_modes import ToolApprovalPlan, decide_child, decide_part
from app.guardrails.approval import with_approvals
from app.guardrails.rules import Rule, RuleSet
from app.guardrails.tool_risk import (
    ToolPolicy,
    ToolRiskPolicy,
    actions_from_json,
    skills_on,
    tool_policy_from_json,
)
from app.marketplace.agent_spec import _portable_tool_approval
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.audit_event import AuditEvent
from app.models.organization import Organization, Tenant
from app.models.resource_policy import ResourcePolicy
from app.models.skill import AgentSkillLink, Skill
from app.services import audit_service
from app.tools.keys import (
    ToolRef,
    agent_ref,
    attach_parts,
    part_named,
    parts_of,
    skill_ref,
)
from app.tools.risk import ToolRiskLevel
from tests.authz.d04_support import OTHER_USER_ID, seed_scope
from tests.authz.d05_support import TEST_ORG_ID
from tests.authz.e09_support import MEMBER, TENANT, acting_as, actions, make_admin
from tests.authz.unit_scope_fixture import TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession, make_user, seed_agent

POLICIES = f"{TENANT}/guardrails/tool-policies"
C, R = ToolRiskLevel.CODE_EXECUTION, ToolRiskLevel.READ_ONLY
RELAXED = {"code_execution": "approval_relaxable"}
ALLOW = {"code_execution": "allow"}
RULE = {"all": [{"source": "args", "path": "/to", "op": "ends_with", "value": "@corp.com"}]}
EXEC = ToolRef("builtin:execute_in_skill", "execute_in_skill")
ENTRY = {"allowed_decisions": ["approve", "reject"]}
SKILL_A = skill_ref({"id": "a1", "slug": "alpha", "name": "Alpha"})
SKILL_B = skill_ref({"id": "b1", "slug": "beta", "name": "Beta"})
CHILD = agent_ref("c1", "researcher")
OTHER_CHILD = agent_ref("c2", "writer")


def _settings(**tools: Any) -> ToolApprovalPolicyV1:
    return ToolApprovalPolicyV1.model_validate({"tools": tools})


def _policy(**raw: Any) -> ToolPolicy:
    return tool_policy_from_json(raw)


def _company(
    actions: dict[str, str] | None = None,
    tools: dict[str, ToolPolicy] | None = None,
    always: frozenset[str] = frozenset(),
) -> ToolRiskPolicy:
    return ToolRiskPolicy(actions_from_json(actions or {}), always, tools or {})


class _SkillTool:
    """``execute_in_skill`` as a run carries it: its risk level and the skills behind it."""

    name = "execute_in_skill"

    def __init__(self, *skills: ToolRef) -> None:
        self.metadata: dict[str, Any] = {"moldy_risk": {"risk_level": C.value}}
        attach_parts(self, {skill.name: skill for skill in skills})  # type: ignore[arg-type]


def _call(tool: str, **args: Any) -> SimpleNamespace:
    return SimpleNamespace(tool_call={"name": tool, "args": args})


def _skill_call(slug: str, **args: Any) -> SimpleNamespace:
    return _call("execute_in_skill", skill_directory=f"/runtime/t1/skills/{slug}", **args)


def _part(
    part: ToolRef = SKILL_A,
    *,
    settings: ToolApprovalPolicyV1 | None = None,
    company: ToolRiskPolicy | None = None,
    grants: tuple[str, ...] = (),
    legacy: tuple[str, ...] = (),
    root: bool = True,
) -> tuple[str, str]:
    found = decide_part(
        part,
        EXEC,
        C,
        "chat",
        settings=settings,
        company=company or _company(RELAXED),
        grants=frozenset(grants),
        legacy_always=frozenset(legacy),
        root=root,
    )
    return found.kind, found.layer


# --- which part a call means ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args", "expected"),
    [
        ("execute_in_skill", {"skill_directory": "/runtime/t/skills/alpha"}, "alpha"),
        ("execute_in_skill", {"skill_directory": "/runtime/t/skills/alpha/"}, "alpha"),
        ("execute_in_skill", {"skill_directory": "alpha"}, "alpha"),
        ("execute_in_skill", {"skill_directory": "/"}, None),
        ("execute_in_skill", {"skill_directory": 3}, None),
        ("execute_in_skill", {"command": "ls"}, None),
        ("execute_in_skill", "alpha", None),
        ("task", {"subagent_type": "researcher"}, "researcher"),
        ("task", {"subagent_type": None}, None),
        ("send_mail", {"skill_directory": "alpha", "subagent_type": "x"}, None),
    ],
)
def test_the_part_is_read_from_the_arguments_the_tool_itself_reads(
    tool: str, args: Any, expected: str | None
) -> None:
    assert part_named(tool, args) == expected


def test_a_skill_and_a_child_agent_are_named_by_their_row() -> None:
    assert (SKILL_A.key, SKILL_A.name, SKILL_A.server, SKILL_A.source) == (
        "skill:a1",
        "alpha",
        "builtin:execute_in_skill",
        "Alpha",
    )
    assert (CHILD.key, CHILD.name, CHILD.server) == ("agent:c1", "researcher", "builtin:task")
    with_default = skill_ref({"id": "d1", "slug": "d", "approval_default": {"mode": "always"}})
    assert with_default.default == {"mode": "always", "rule": None, "layer": "tool"}
    assert parts_of(_SkillTool(SKILL_A)) == {"alpha": SKILL_A}  # type: ignore[arg-type]
    assert parts_of("execute_in_skill") == {}


# --- the decision for one skill -------------------------------------------------------------


def test_a_skill_follows_the_whole_tool_until_something_names_the_skill() -> None:
    assert _part() == ("ask", "risk_default")
    assert _part(company=_company()) == ("ask", "company_forced")
    assert _part(company=_company(ALLOW)) == ("auto", "risk_default")
    # What was said about ``execute_in_skill`` (by key or by name) reaches every skill…
    by_key = _settings(**{"builtin:execute_in_skill": {"mode": "never"}})
    by_name = _settings(execute_in_skill={"mode": "never"})
    assert _part(settings=by_key) == ("auto", "tool_never")
    assert _part(settings=by_name) == ("auto", "tool_never")
    # …and the skill's own setting wins over it, either way round.
    asks = _settings(**{"skill:a1": {"mode": "always"}, "execute_in_skill": {"mode": "never"}})
    lifts = _settings(**{"skill:a1": {"mode": "never"}, "execute_in_skill": {"mode": "always"}})
    assert _part(settings=asks) == ("ask", "tool_always")
    assert _part(SKILL_B, settings=asks) == ("auto", "tool_never")
    assert _part(settings=lifts) == ("auto", "tool_never")
    assert _part(SKILL_B, settings=lifts) == ("ask", "tool_always")


def test_a_setting_saved_under_a_name_never_means_the_skill_carrying_it() -> None:
    named = _settings(alpha={"mode": "never"}, Alpha={"mode": "never"})
    assert _part(settings=named) == ("ask", "risk_default")
    assert _part(grants=("alpha",)) == ("ask", "risk_default")
    assert _part(company=_company(ALLOW), legacy=("alpha",)) == ("auto", "risk_default")


def test_grants_and_the_older_always_list_reach_a_skill_through_its_tool_or_its_key() -> None:
    assert _part(grants=("skill:a1",)) == ("auto", "conversation_grant")
    assert _part(grants=("execute_in_skill",)) == ("auto", "conversation_grant")
    assert _part(grants=("skill:b1",)) == ("ask", "risk_default")
    assert _part(company=_company(ALLOW), legacy=("execute_in_skill",)) == ("ask", "tool_always")
    # Under the company's own default nothing an agent or a conversation says lifts the ask.
    forced = _company()
    assert _part(company=forced, grants=("skill:a1",)) == ("ask", "company_forced")
    assert _part(company=forced, settings=_settings(**{"skill:a1": {"mode": "never"}})) == (
        "ask",
        "company_forced",
    )


def test_the_company_decides_a_skill_before_anything_below_it() -> None:
    never = _settings(**{"skill:a1": {"mode": "never"}})
    off = _company(RELAXED, {"skill:a1": _policy(enabled=False)})
    assert _part(settings=never, company=off) == ("hidden", "company_tool_disabled")
    assert _part(SKILL_B, settings=never, company=off) == ("ask", "risk_default")
    locked = _company(RELAXED, {"skill:a1": _policy(allowed_modes=["always"])})
    assert _part(settings=never, company=locked, grants=("skill:a1",)) == (
        "ask",
        "company_tool_locked",
    )
    assert _part(company=locked, root=False) == ("ask", "company_tool_locked")
    # The company may let one skill run without asking inside a level it forces.
    free = _company({}, {"skill:a1": _policy(allowed_modes=["never"])})
    assert _part(company=free) == ("auto", "company_tool_locked")
    assert _part(SKILL_B, company=free) == ("ask", "company_forced")
    # A policy on ``execute_in_skill`` itself is every skill's, unless the skill has its own.
    whole = _company(RELAXED, {"builtin:execute_in_skill": _policy(default_mode="always")})
    assert _part(settings=never, company=whole) == ("auto", "tool_never")
    assert _part(SKILL_B, company=whole) == ("ask", "company_tool_default")
    listed = _company(ALLOW, always=frozenset({"execute_in_skill"}))
    assert _part(settings=never, company=listed) == ("ask", "company_always")


def test_a_skills_own_default_asks_but_lifts_only_for_the_root_agent() -> None:
    asking = skill_ref({"id": "a1", "slug": "alpha", "approval_default": {"mode": "always"}})
    free = skill_ref({"id": "a1", "slug": "alpha", "approval_default": {"mode": "never"}})
    broken = skill_ref({"id": "a1", "slug": "alpha", "approval_default": {"mode": "sometimes"}})
    allow = _company(ALLOW)
    assert _part(asking, company=allow) == ("ask", "tool_default")
    assert _part(asking, company=allow, root=False) == ("ask", "tool_default")
    assert _part(free) == ("auto", "tool_default")
    assert _part(free, root=False) == ("ask", "risk_default")
    assert _part(free, company=_company()) == ("ask", "company_forced")
    assert _part(broken, company=allow) == ("ask", "tool_default")  # unreadable: asks
    # The agent's own word about the skill comes before the skill's default.
    assert _part(asking, settings=_settings(**{"skill:a1": {"mode": "never"}})) == (
        "auto",
        "tool_never",
    )


def test_a_switched_off_skill_is_not_given_to_a_run() -> None:
    skills = [{"id": "a1", "slug": "alpha"}, {"id": "b1", "slug": "beta"}]
    one_off = _company(RELAXED, {"skill:a1": _policy(enabled=False)})
    all_off = _company(RELAXED, {"builtin:execute_in_skill": _policy(enabled=False)})
    kept_on = _company(
        RELAXED,
        {"builtin:execute_in_skill": _policy(enabled=False), "skill:b1": _policy(enabled=True)},
    )
    asking = _company(RELAXED, {"skill:a1": _policy(allowed_modes=["always"])})

    assert skills_on(one_off, skills) == [skills[1]]
    assert skills_on(all_off, skills) == []
    assert skills_on(kept_on, skills) == [skills[1]]
    assert skills_on(asking, skills) == skills
    assert skills_on(None, skills) == skills
    assert skills_on(_company(RELAXED), skills) == skills
    assert one_off.switched_off(SKILL_A) and not one_off.switched_off(SKILL_B)
    assert not asking.switched_off(SKILL_A)
    # A blocked level alone is not "this very skill was switched off".
    assert not _company({"code_execution": "block"}).switched_off(SKILL_A)


# --- delegation to a child agent ------------------------------------------------------------


def _child(
    child: ToolRef = CHILD,
    *,
    settings: ToolApprovalPolicyV1 | None = None,
    company: ToolRiskPolicy | None = None,
    grants: tuple[str, ...] = (),
) -> tuple[str, str]:
    found = decide_child(
        child, "chat", settings=settings, company=company, grants=frozenset(grants)
    )
    return found.kind, found.layer


def test_delegation_asks_only_where_someone_said_so() -> None:
    assert _child() == ("auto", "risk_default")
    # The company's risk actions are about tools; handing work over has no level of its own.
    assert _child(company=_company()) == ("auto", "risk_default")
    assert _child(company=_company({"read_only": "approval", "unknown": "block"})) == (
        "auto",
        "risk_default",
    )
    assert _child(settings=_settings(**{"agent:c1": {"mode": "always"}})) == ("ask", "tool_always")
    assert _child(OTHER_CHILD, settings=_settings(**{"agent:c1": {"mode": "always"}})) == (
        "auto",
        "risk_default",
    )
    assert _child(settings=_settings(task={"mode": "always"})) == ("ask", "tool_always")
    assert _child(settings=_settings(researcher={"mode": "always"})) == ("auto", "risk_default")
    always = _settings(**{"agent:c1": {"mode": "always"}})
    assert _child(settings=always, grants=("agent:c1",)) == ("ask", "tool_always")


def test_the_company_decides_delegation_to_an_agent() -> None:
    asks = _company({}, {"agent:c1": _policy(default_mode="always")})
    locked = _company({}, {"agent:c1": _policy(allowed_modes=["always"])})
    off = _company({}, {"agent:c1": _policy(enabled=False)})
    every = _company({}, {"builtin:task": _policy(default_mode="always")})
    never = _settings(**{"agent:c1": {"mode": "never"}})

    assert _child(company=asks) == ("ask", "company_tool_default")
    assert _child(company=asks, settings=never) == ("auto", "tool_never")
    assert _child(company=locked, settings=never) == ("ask", "company_tool_locked")
    assert _child(OTHER_CHILD, company=locked) == ("auto", "risk_default")
    assert _child(company=off) == ("hidden", "company_tool_disabled")
    assert _child(company=every) == ("ask", "company_tool_default")
    assert _child(OTHER_CHILD, company=every) == ("ask", "company_tool_default")
    assert _child(company=_company(always=frozenset({"task"}))) == ("ask", "company_always")


# --- the run's interrupt entries ------------------------------------------------------------


def _apply(
    plan: ToolApprovalPlan,
    *skills: ToolRef,
    interrupt_on: dict[str, Any] | None = None,
    root: bool = True,
    children: dict[str, ToolRef] | None = None,
) -> tool_modes.Applied:
    start = {"execute_in_skill": dict(ENTRY)} if interrupt_on is None else interrupt_on
    return tool_modes.apply(
        start,
        [_SkillTool(*skills)],  # type: ignore[list-item]
        plan,
        root=root,
        children=children,
    )


def test_one_skill_runs_without_asking_and_the_others_still_ask() -> None:
    plan = ToolApprovalPlan(
        "chat",
        settings=_settings(**{"skill:a1": {"mode": "never"}}),
        company=_company(RELAXED),
    )
    applied = _apply(plan, SKILL_A, SKILL_B)
    entry = applied.interrupt_on["execute_in_skill"]  # type: ignore[index]
    when = entry["when"]

    assert when(_skill_call("alpha")) is False
    assert when(_skill_call("beta")) is True
    assert when(_skill_call("gamma")) is True  # not one of this run's skills: the tool's answer
    assert when(_call("execute_in_skill", command="ls")) is True
    assert when(SimpleNamespace(tool_call={"name": "execute_in_skill", "args": "alpha"})) is True
    assert when(SimpleNamespace(tool_call=None)) is True  # unreadable: asks
    assert when(_skill_call("alpha")) is False  # the same answer when a resume asks again
    assert entry["allowed_decisions"] == ["approve", "reject"]
    text = entry["description"](
        {"name": "execute_in_skill", "args": {"skill_directory": "/skills/beta"}}, None, None
    )
    assert "execute_in_skill" in text and "/skills/beta" in text
    assert applied.auto_allowed == {}
    assert applied.auto_parts == {"execute_in_skill": {"alpha": ("skill:a1", "tool_never")}}


def test_the_whole_tool_set_free_keeps_the_skill_that_asks() -> None:
    plan = ToolApprovalPlan(
        "chat",
        settings=_settings(
            execute_in_skill={"mode": "never"},
            **{"skill:b1": {"mode": "when", "rule": RULE}},
        ),
        company=_company(RELAXED),
    )
    applied = _apply(plan, SKILL_A, SKILL_B)
    when = applied.interrupt_on["execute_in_skill"]["when"]  # type: ignore[index]

    assert when(_skill_call("alpha")) is False
    assert when(_skill_call("beta", to="x@corp.com")) is True
    assert when(_skill_call("beta", to="x@else.com")) is False
    assert when(_skill_call("gamma")) is False  # the whole tool was set free
    assert applied.auto_parts == {"execute_in_skill": {"alpha": ("skill:a1", "tool_never")}}


def test_skills_that_say_nothing_leave_the_tools_entry_as_it_was() -> None:
    relaxed = ToolApprovalPlan("chat", settings=_settings(), company=_company(RELAXED))
    free = ToolApprovalPlan(
        "chat", settings=_settings(execute_in_skill={"mode": "never"}), company=_company(RELAXED)
    )
    every = ToolApprovalPlan(
        "chat",
        settings=_settings(**{"skill:a1": {"mode": "never"}, "skill:b1": {"mode": "never"}}),
        company=_company(RELAXED),
    )

    untouched = _apply(relaxed, SKILL_A, SKILL_B).interrupt_on or {}
    assert untouched["execute_in_skill"] == ENTRY
    lifted = _apply(free, SKILL_A, SKILL_B)
    assert "execute_in_skill" not in (lifted.interrupt_on or {})
    assert lifted.auto_allowed == {"execute_in_skill": "tool_never"}
    assert lifted.auto_parts == {}
    # Every skill set free one by one: a call naming none of them still asks.
    each = _apply(every, SKILL_A, SKILL_B)
    when = each.interrupt_on["execute_in_skill"]["when"]  # type: ignore[index]
    assert (when(_skill_call("alpha")), when(_skill_call("gamma"))) == (False, True)


def test_a_skills_default_adds_an_ask_where_the_tool_has_none() -> None:
    asking = skill_ref({"id": "a1", "slug": "alpha", "approval_default": {"mode": "always"}})
    plan = ToolApprovalPlan("chat", settings=None, company=_company(ALLOW), defaults=True)
    applied = _apply(plan, asking, SKILL_B, interrupt_on={})
    when = applied.interrupt_on["execute_in_skill"]["when"]  # type: ignore[index]

    assert (when(_skill_call("alpha")), when(_skill_call("beta"))) == (True, False)
    assert applied.auto_parts == {"execute_in_skill": {"beta": ("skill:b1", "risk_default")}}
    assert plan.acts is True


def test_a_child_agent_gets_the_asks_a_skill_adds_and_none_of_the_lifts() -> None:
    asking = skill_ref({"id": "a1", "slug": "alpha", "approval_default": {"mode": "always"}})
    free = skill_ref({"id": "b1", "slug": "beta", "approval_default": {"mode": "never"}})
    lifting = ToolApprovalPlan(
        "chat",
        settings=_settings(
            **{"skill:a1": {"mode": "never"}, "execute_in_skill": {"mode": "never"}}
        ),
        company=_company(RELAXED),
        grants=frozenset({"skill:a1"}),
    )
    # The child asks already: nothing the root's settings or a skill's default say lifts it.
    kept = _apply(lifting, SKILL_A, free, root=False)
    assert kept.interrupt_on == {"execute_in_skill": ENTRY}
    assert kept.auto_parts == {} and kept.auto_allowed == {}

    # The child does not ask (the company allows the level): a skill's default adds the ask.
    allow = ToolApprovalPlan("chat", settings=None, company=_company(ALLOW), defaults=True)
    added = _apply(allow, asking, free, interrupt_on={}, root=False)
    when = added.interrupt_on["execute_in_skill"]["when"]  # type: ignore[index]
    assert (when(_skill_call("alpha")), when(_skill_call("beta"))) == (True, False)
    assert when(_skill_call("gamma")) is False

    # The company's own word about one skill reaches the child either way.
    company = _company(
        RELAXED,
        {"skill:a1": _policy(allowed_modes=["never"]), "skill:b1": _policy(enabled=False)},
    )
    fixed = _apply(ToolApprovalPlan("chat", company=company), SKILL_A, SKILL_B, root=False)
    when = fixed.interrupt_on["execute_in_skill"]["when"]  # type: ignore[index]
    assert when(_skill_call("alpha")) is False
    assert when(_skill_call("beta")) is True  # switched off since: never runs unasked
    assert when(_skill_call("gamma")) is True

    # …also where the child had no entry: the switched-off skill asks, and what was not
    # asked before stays unasked (only the company and a default add an ask).
    off = _company(ALLOW, {"skill:b1": _policy(enabled=False)})
    bare = _apply(
        ToolApprovalPlan("chat", company=off), SKILL_A, SKILL_B, interrupt_on={}, root=False
    )
    when = bare.interrupt_on["execute_in_skill"]["when"]  # type: ignore[index]
    assert (when(_skill_call("alpha")), when(_skill_call("beta"))) == (False, True)
    relaxed = ToolApprovalPlan("chat", company=_company(RELAXED), defaults=True)
    only = _apply(relaxed, asking, SKILL_B, interrupt_on={}, root=False)
    when = only.interrupt_on["execute_in_skill"]["when"]  # type: ignore[index]
    assert (when(_skill_call("alpha")), when(_skill_call("beta"))) == (True, False)


def test_delegation_gets_an_entry_only_when_a_child_asks() -> None:
    children = {"researcher": CHILD, "writer": OTHER_CHILD}
    quiet = ToolApprovalPlan("chat", settings=_settings(), company=_company())
    asks = ToolApprovalPlan(
        "chat", settings=_settings(**{"agent:c1": {"mode": "always"}}), company=_company()
    )
    company = ToolApprovalPlan(
        "trigger", company=_company({}, {"agent:c2": _policy(default_mode="always")})
    )

    assert "task" not in _apply(quiet, children=children).interrupt_on  # type: ignore[operator]
    assert "task" not in _apply(asks).interrupt_on  # type: ignore[operator]  # no children
    entry = _apply(asks, children=children).interrupt_on["task"]  # type: ignore[index]
    assert entry["allowed_decisions"] == ["approve", "reject"]
    when = entry["when"]
    assert when(_call("task", subagent_type="researcher", description="x")) is True
    assert when(_call("task", subagent_type="writer")) is False
    assert when(_call("task", subagent_type="general-purpose")) is False
    assert when(_call("task", description="x")) is False
    assert when(SimpleNamespace(tool_call=None)) is True
    by_company = _apply(company, children=children).interrupt_on["task"]["when"]  # type: ignore[index]
    assert by_company(_call("task", subagent_type="writer")) is True
    assert by_company(_call("task", subagent_type="researcher")) is False
    # Delegation is never reported as a risky call that ran unasked.
    assert _apply(asks, children=children).auto_parts == {}


def test_a_guardrail_rule_still_asks_for_a_skill_that_runs_without_asking() -> None:
    rules = RuleSet(
        "company:approval",
        1,
        (Rule("rrn-approval", ("kr",), frozenset({"tool_args"}), "approval", frozenset({"RRN"})),),
    )
    plan = ToolApprovalPlan(
        "chat", settings=_settings(**{"skill:a1": {"mode": "never"}}), company=_company(RELAXED)
    )
    applied = _apply(plan, SKILL_A, SKILL_B)
    merged = with_approvals(
        applied.interrupt_on or {},
        [_SkillTool(SKILL_A, SKILL_B)],  # type: ignore[list-item]
        rules,
    )
    entry = merged["execute_in_skill"]

    assert entry["when"](_skill_call("alpha", command="echo hi")) is False
    assert entry["when"](_skill_call("alpha", command="주민번호 900101-1234567")) is True
    assert entry["when"](_skill_call("beta", command="echo hi")) is True
    rule_text = entry["description"](
        {
            "name": "execute_in_skill",
            "args": {"skill_directory": "/s/alpha", "command": "주민번호 900101-1234567"},
        },
        None,
        None,
    )
    own_text = entry["description"](
        {"name": "execute_in_skill", "args": {"skill_directory": "/s/beta"}}, None, None
    )
    assert "가드레일" in rule_text and "가드레일" not in own_text


async def test_a_skill_run_without_asking_is_audited_with_the_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[dict[str, Any]] = []

    async def record(_db: Any, **kwargs: Any) -> None:
        recorded.append(kwargs)

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def commit(self) -> None:
            return None

    monkeypatch.setattr("app.services.audit_service.record_event", record)
    monkeypatch.setattr("app.database.tenant_session", lambda *_a, **_k: _Session())
    target = tool_modes.AuditTarget(TEST_TENANT_ID, TEST_ORG_ID, TEST_USER_ID, "agent-1", "t-1")
    middleware = tool_modes.AutoAllowedAuditMiddleware(
        {}, target, {"execute_in_skill": {"alpha": ("skill:a1", "tool_never")}}
    )

    async def handler(_request: Any) -> str:
        return "ran"

    assert await middleware.awrap_tool_call(_skill_call("alpha"), handler) == "ran"
    assert await middleware.awrap_tool_call(_skill_call("beta"), handler) == "ran"
    assert await middleware.awrap_tool_call(_call("execute_in_skill"), handler) == "ran"

    assert [(r["action"], r["reason_code"]) for r in recorded] == [
        ("approval.auto_allowed", "tool_never")
    ]
    assert recorded[0]["metadata"]["tool"] == "execute_in_skill"
    assert recorded[0]["metadata"]["part"] == "skill:a1"


def test_a_published_agent_does_not_carry_settings_about_the_publishers_rows() -> None:
    agent = SimpleNamespace(mcp_tool_links=[], tool_links=[])
    portable = _portable_tool_approval(
        agent,  # type: ignore[arg-type]
        {
            "skill:a1": {"mode": "never"},
            "agent:c1": {"mode": "always"},
            "builtin:execute_in_skill": {"mode": "always"},
            "execute_in_skill": {"mode": "never"},
        },
    )
    assert portable == {
        "builtin:execute_in_skill": {"mode": "always"},
        "execute_in_skill": {"mode": "never"},
    }


# --- the API --------------------------------------------------------------------------------


def _skill(name: str, **values: Any) -> Skill:
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": TEST_USER_ID,
        "name": name,
        "slug": name.lower().replace(" ", "-"),
        "description": None,
        "kind": "text",
        "storage_path": None,
        "content_hash": None,
        "size_bytes": 0,
        "version": None,
        "package_metadata": None,
        "used_by_count": 0,
        "is_system": False,
        "source_kind": None,
        "origin_kind": "created_by_me",
        "is_dirty": False,
        "org_id": TEST_ORG_ID,
        "tenant_id": TEST_TENANT_ID,
    }
    return Skill(**(fields | values))


async def _default_events(db: AsyncSession) -> list[AuditEvent]:
    rows = await db.scalars(
        sa.select(AuditEvent)
        .where(AuditEvent.action == "tool.approval_default_updated")
        .order_by(AuditEvent.created_at, AuditEvent.id)
    )
    return list(rows)


async def test_a_skill_editor_sets_and_clears_the_skills_default(
    client: AsyncClient, db: AsyncSession
) -> None:
    await seed_agent(db)
    skill = _skill("Report")
    db.add(skill)
    await db.commit()
    url = f"/api/skills/{skill.id}/approval-default"

    assert (await client.put(url, json={"default": {"mode": "when"}})).status_code == 422
    assert (await client.put(url, json={"default": {"mode": "never"}, "x": 1})).status_code == 422
    assert (await client.put(url, json={})).status_code == 422
    missing = f"/api/skills/{uuid.uuid4()}/approval-default"
    assert (await client.put(missing, json={"default": None})).status_code == 404

    set_ = await client.put(url, json={"default": {"mode": "when", "rule": RULE}})
    assert set_.status_code == 200, set_.text
    assert set_.json()["approval_default"]["mode"] == "when"
    again = await client.put(url, json={"default": {"mode": "always"}})
    assert again.json()["approval_default"] == {"mode": "always"}
    assert (await client.get(f"/api/skills/{skill.id}")).json()["approval_default"] == {
        "mode": "always"
    }
    cleared = await client.put(url, json={"default": None})
    assert cleared.status_code == 200 and cleared.json()["approval_default"] is None

    rows = await _default_events(db)
    metas = [row.event_metadata or {} for row in rows]
    assert sorted((meta["before"] or "", meta["after"] or "") for meta in metas) == [
        ("", "when"),
        ("always", ""),
        ("when", "always"),
    ]
    assert {(str(row.target_id), row.outcome) for row in rows} == {(str(skill.id), "success")}
    assert all(meta["scope"] == "skill" for meta in metas)
    assert "corp.com" not in str(metas)  # modes, never the rule


async def test_another_persons_skill_keeps_its_default(
    client: AsyncClient, db: AsyncSession
) -> None:
    await seed_agent(db)
    other = await make_user(db)
    skill = _skill("Theirs", user_id=other.id)
    db.add(skill)
    await db.commit()

    refused = await client.put(
        f"/api/skills/{skill.id}/approval-default", json={"default": {"mode": "never"}}
    )

    assert refused.status_code == 404
    async with TestSession() as check:
        assert (await check.get(Skill, skill.id)).approval_default is None  # type: ignore[union-attr]
    assert await _default_events(db) == []


async def _agent_with_parts(db: AsyncSession) -> tuple[Agent, Skill, Agent, Agent]:
    _, _, agent = await seed_agent(db, name="Parent")
    _, _, child = await seed_agent(db, name="Researcher")
    _, _, hidden = await seed_agent(db, name="Builder", runtime_profile="skill_builder")
    skill = _skill("Report", approval_default={"mode": "always"})
    db.add(skill)
    await db.flush()
    db.add(AgentSkillLink(agent_id=agent.id, skill_id=skill.id))
    db.add(AgentSubAgentLink(parent_agent_id=agent.id, sub_agent_id=child.id, position=0))
    db.add(AgentSubAgentLink(parent_agent_id=agent.id, sub_agent_id=hidden.id, position=1))
    await db.commit()
    return agent, skill, child, hidden


async def test_the_agents_table_lists_its_skills_and_child_agents(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.routers import tool_approval as route

    agent, skill, child, hidden = await _agent_with_parts(db)
    skill_key, child_key = f"skill:{skill.id}", f"agent:{child.id}"

    forced = (await client.get(f"/api/agents/{agent.id}/tool-approval")).json()
    parts = {part["key"]: part for part in forced["parts"]}
    assert list(parts) == [skill_key, child_key]  # skills first; a hidden agent is no child
    assert f"agent:{hidden.id}" not in parts
    assert (parts[skill_key]["name"], parts[skill_key]["kind"], parts[skill_key]["level"]) == (
        "Report",
        "skill",
        "code_execution",
    )
    assert {cell["layer"] for cell in parts[skill_key]["cells"]} == {"company_forced"}
    assert parts[skill_key]["locked"] is True
    assert (parts[child_key]["name"], parts[child_key]["kind"], parts[child_key]["level"]) == (
        "Researcher",
        "agent",
        None,
    )
    assert sorted(
        (cell["path"], cell["kind"], cell["layer"]) for cell in parts[child_key]["cells"]
    ) == [
        ("api", "auto", "risk_default"),
        ("chat", "auto", "risk_default"),
        ("trigger", "auto", "risk_default"),
        ("workflow", "auto", "risk_default"),
    ]
    assert parts[child_key]["locked"] is False

    async def company(*_: Any) -> ToolRiskPolicy:
        return _company(
            RELAXED,
            {child_key: _policy(allowed_modes=["always", "when"], default_mode="always")},
        )

    monkeypatch.setattr(route, "resolve_tool_risk", company)
    agent.runtime_policy = {
        "version": 1,
        "tool_approval": {"tools": {skill_key: {"mode": "never"}, "Report": {"mode": "always"}}},
    }
    await db.commit()
    view = (await client.get(f"/api/agents/{agent.id}/tool-approval")).json()
    parts = {part["key"]: part for part in view["parts"]}
    assert {cell["layer"] for cell in parts[skill_key]["cells"]} == {"tool_never"}
    assert {(cell["kind"], cell["layer"]) for cell in parts[child_key]["cells"]} == {
        ("ask", "company_tool_default")
    }
    assert parts[child_key]["allowed_modes"] == ["always", "when"]
    # The key entry is the skill's; the name "Report" means no tool of this agent.
    assert view["settings"]["tools"] == {skill_key: {"mode": "never"}}
    assert view["unresolved"] == ["Report"]


@pytest.fixture
async def _member(db: AsyncSession, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch):
    # A refused write is audited in a session of its own; keep it in the test database.
    monkeypatch.setattr(audit_service, "async_session", TestSession)
    await seed_scope(db)
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        yield


async def _stored(key: str) -> ResourcePolicy | None:
    async with TestSession() as check:
        return await check.scalar(
            sa.select(ResourcePolicy).where(ResourcePolicy.resource_key == key)
        )


@pytest.mark.usefixtures("_member")
async def test_a_company_policy_can_name_a_skill_or_an_agent(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    agent, skill, child, hidden = await _agent_with_parts(db)
    skill_key, child_key = f"skill:{skill.id}", f"agent:{child.id}"

    found = {item["key"]: item for item in (await client.get(f"{POLICIES}/targets")).json()}
    assert found[skill_key] == {
        "key": skill_key,
        "name": "Report",
        "kind": "skill",
        "server_key": "builtin:execute_in_skill",
        "source": None,
        "risk_level": "code_execution",
    }
    assert found[child_key] == {
        "key": child_key,
        "name": "Researcher",
        "kind": "agent",
        "server_key": "builtin:task",
        "source": None,
        "risk_level": None,
    }
    assert f"agent:{hidden.id}" not in found

    risky = await client.put(f"{POLICIES}/{skill_key}", json={"allowed_modes": ["never"]})
    handed = await client.put(f"{POLICIES}/{child_key}", json={"default_mode": "never"})
    assert risky.status_code == 200, risky.text
    assert risky.json()["warnings"] == [
        {"code": "risky_without_asking", "risk_level": "code_execution"}
    ]
    assert handed.status_code == 200, handed.text
    assert handed.json()["warnings"] == []  # delegation itself changes nothing
    stored = await _stored(skill_key)
    assert stored is not None and stored.allowed_modes == ["never"]
    [event] = [e for e in await actions(skill_key) if e.action == "resource_policy.updated"]
    assert event.event_metadata is not None
    assert event.event_metadata["after"]["allowed_modes"] == ["never"]

    # What a run of this company reads.
    from app.guardrails.policy import tool_policies

    async with TestSession() as check:
        loaded = await tool_policies(check, TEST_TENANT_ID)
    assert loaded[skill_key].allowed == frozenset({"never"})
    assert loaded[child_key].default_mode == "never"


@pytest.mark.usefixtures("_member")
async def test_a_policy_names_no_hidden_archived_or_foreign_part(
    client: AsyncClient, db: AsyncSession
) -> None:
    from datetime import UTC, datetime

    await make_admin(db)
    _, _, _, hidden = await _agent_with_parts(db)
    _, _, archived = await seed_agent(db, name="Old")
    archived.archived_at = datetime.now(UTC)
    tenant = Tenant(id=uuid.uuid4(), name="Other", slug="other-co", secrets_namespace="tenants/o")
    db.add(tenant)
    await db.flush()
    org = Organization(id=uuid.uuid4(), tenant_id=tenant.id, name="Other org", slug="other-org")
    db.add(org)
    await db.flush()
    foreign = _skill("Theirs", user_id=OTHER_USER_ID, org_id=org.id, tenant_id=tenant.id)
    db.add(foreign)
    await db.commit()

    keys = [
        f"agent:{hidden.id}",
        f"agent:{archived.id}",
        f"skill:{foreign.id}",
        f"skill:{uuid.uuid4()}",
        "agent:not-an-id",
    ]
    listed = {item["key"] for item in (await client.get(f"{POLICIES}/targets")).json()}
    for key in keys:
        saved = await client.put(f"{POLICIES}/{key}", json={"enabled": False})
        preview = await client.post(f"{POLICIES}/{key}/impact", json={"enabled": False})
        assert (saved.status_code, preview.status_code) == (404, 404), key
        assert await _stored(key) is None
        assert key not in listed


@pytest.mark.usefixtures("_member")
async def test_the_impact_of_a_skill_policy_is_found_by_key_alone(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    agent, skill, child, _ = await _agent_with_parts(db)
    skill_key, child_key = f"skill:{skill.id}", f"agent:{child.id}"
    agent.runtime_policy = {
        "version": 1,
        "tool_approval": {
            "tools": {
                skill_key: {"mode": "never"},
                child_key: {"mode": "never"},
                "Report": {"mode": "never"},
                "report": {"mode": "never"},
            }
        },
    }
    await db.commit()

    narrowed = await client.post(
        f"{POLICIES}/{skill_key}/impact", json={"allowed_modes": ["always"]}
    )
    handed = await client.post(f"{POLICIES}/{child_key}/impact", json={"allowed_modes": ["always"]})

    assert narrowed.status_code == 200, narrowed.text
    assert [
        (item["tool_key"], item["mode"], item["reason"]) for item in narrowed.json()["agents"]
    ] == [(skill_key, "never", "mode_not_allowed")]
    assert [(item["tool_key"], item["mode"]) for item in handed.json()["agents"]] == [
        (child_key, "never")
    ]
