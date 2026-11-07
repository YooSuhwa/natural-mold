"""Memory policy plan 15 (5.11, Q9): tenant defaults, approval modes and write decisions."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent_runtime.memory_paths import state_has_outside_content
from app.services.memory_policy import (
    decide_record_write,
    effective_mode,
    memory_policy_defaults,
    mode_axes,
    tenant_memory_policy,
)


def test_company_and_personal_tenants_share_keys_and_differ_only_in_values() -> None:
    company, personal = memory_policy_defaults("company"), memory_policy_defaults("personal")
    assert company.keys() == personal.keys()
    assert (company["default_mode"], personal["default_mode"]) == ("approve_all", "partial_auto")
    assert (company["consolidation_enabled"], personal["consolidation_enabled"]) == (False, True)
    assert company["allowed_modes"] == personal["allowed_modes"]


def test_stored_tenant_values_override_defaults_and_bad_values_are_ignored() -> None:
    policy = tenant_memory_policy(
        {
            "kind": "personal",
            "memory": {
                "allowed_modes": ["approve_all", "read_only", "bogus"],
                "consolidation_enabled": False,
            },
        }
    )
    assert policy.allowed_modes == frozenset({"approve_all", "read_only"})
    assert policy.default_mode == "approve_all"  # partial_auto is no longer allowed
    assert policy.consolidation_enabled is False


@pytest.mark.parametrize(
    ("enabled", "mode", "legacy", "expected"),
    [
        (False, "partial_auto", "auto", "off"),
        (True, None, "off", "read_only"),
        (True, None, "auto", "partial_auto"),
        (True, None, "ask", "approve_all"),  # the old default takes the company default
        (True, "auto_notify", "ask", "auto_notify"),
    ],
)
def test_effective_mode_maps_saved_settings(
    enabled: bool, mode: str | None, legacy: str, expected: str
) -> None:
    tenant = tenant_memory_policy({"kind": "company"})
    assert (
        effective_mode(
            memory_enabled=enabled, approval_mode=mode, legacy_write_policy=legacy, tenant=tenant
        )
        == expected
    )


def test_a_mode_the_tenant_disallows_falls_back_to_the_tenant_default() -> None:
    tenant = tenant_memory_policy({"memory": {"allowed_modes": ["approve_all"]}})
    assert (
        effective_mode(
            memory_enabled=True,
            approval_mode="auto_notify",
            legacy_write_policy="ask",
            tenant=tenant,
        )
        == "approve_all"
    )


def _decide(mode: str, *, origin: str = "agent_inferred", flags=(), path: str = "chat"):
    return decide_record_write(
        mode=mode, axes=mode_axes(mode), run_path=path, origin=origin, flags=flags
    )


@pytest.mark.parametrize(
    ("mode", "origin", "flags", "outcome", "review"),
    [
        ("partial_auto", "agent_inferred", (), "save", False),
        ("partial_auto", "tool_derived", (), "propose", False),
        ("partial_auto", "agent_inferred", ("sensitive",), "propose", False),
        ("partial_auto", "agent_inferred", ("other_person",), "propose", False),
        ("approve_all", "agent_inferred", (), "propose", False),
        ("read_only", "agent_inferred", (), "reject", False),
        ("off", "agent_inferred", (), "reject", False),
        ("auto_notify", "tool_derived", (), "save", True),
        ("auto_notify", "agent_inferred", ("instruction_like",), "propose", False),
        ("partial_auto", "user_stated", ("sensitive",), "save", False),
    ],
)
def test_record_write_decisions(
    mode: str, origin: str, flags: tuple, outcome: str, review: bool
) -> None:
    decision = _decide(mode, origin=origin, flags=flags)
    assert (decision.outcome, decision.review_later) == (outcome, review)


@pytest.mark.parametrize(("path", "outcome"), [("trigger", "propose"), ("api", "reject")])
def test_paths_cap_every_mode(path: str, outcome: str) -> None:
    assert _decide("auto_notify", path=path).outcome == outcome


def test_a_schedule_with_writes_off_rejects() -> None:
    decision = decide_record_write(
        mode="partial_auto",
        axes=mode_axes("partial_auto"),
        run_path="trigger",
        origin="agent_inferred",
        flags=(),
        trigger_writes_enabled=False,
    )
    assert decision.outcome == "reject"


def test_custom_axes_default_to_approve() -> None:
    assert mode_axes("custom", {"records": "auto", "profile": "bogus"}) == {
        "records": "auto",
        "profile": "approve",
        "consolidation": "approve",
    }


@pytest.mark.parametrize(
    ("messages", "tainted"),
    [
        ([HumanMessage("hi"), AIMessage("hello")], False),
        ([ToolMessage("noted", tool_call_id="1", name="write_todos")], False),
        ([ToolMessage("page text", tool_call_id="1", name="web_search")], True),
        ([ToolMessage("child said", tool_call_id="1", name="task")], True),
        (
            [
                HumanMessage(
                    [{"type": "text", "text": "a"}, {"type": "image_url", "image_url": "x"}]
                )
            ],
            True,
        ),
        ([HumanMessage("Here is a summary of the conversation to date:\n\n...")], True),
    ],
)
def test_outside_content_detection(messages: list, tainted: bool) -> None:
    assert state_has_outside_content(messages) is tainted
