"""P3 foundation: plan 8.3 route policy markers form one tagged union matched on ``kind``."""

import pytest

from app.authz import policies
from app.authz.policies import (
    CANDIDATES,
    CAP,
    KEY,
    PRIVATE,
    RES,
    SELF,
    SHARE_TOKEN,
    authz_policy,
    policy_kind,
    policy_of,
)


def test_factories_build_frozen_markers_with_their_kind():
    res = RES("agent", "can_edit", param="agent_id", min_visible="can_run")
    assert (res.kind, res.type_, res.permission, res.param, res.min_visible) == (
        "RES",
        "agent",
        "can_edit",
        "agent_id",
        "can_run",
    )
    assert CAP("cap_create_agent").kind == "CAP"
    assert PRIVATE("conversation", param="conversation_id").param == "conversation_id"
    assert KEY().permission == "can_deploy" and KEY().kind == "KEY"
    assert SHARE_TOKEN() == SHARE_TOKEN() and SHARE_TOKEN().kind == "SHARE_TOKEN"
    assert CANDIDATES("skill", "can_use").kind == "CANDIDATES"
    with pytest.raises(AttributeError):
        res.permission = "can_run"  # type: ignore[misc]


def test_min_visible_is_required_and_kind_is_not_settable():
    with pytest.raises(TypeError):
        RES("agent", "can_edit", param="agent_id")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        policies.ResourcePolicy("agent", "x", "p", "v", kind="CAP")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="unknown bare"):
        policies.KindPolicy("RES")  # type: ignore[arg-type]


def test_bare_kinds_keep_every_existing_name():
    names = ("LEGACY", "E2E_ONLY", "PUBLIC", "SELF", "CATALOG", "SUPER", "GOVERNANCE")
    assert [getattr(policies, name).kind for name in names] == list(names)


def test_decorator_attaches_the_marker_and_rejects_bare_strings():
    async def endpoint() -> None: ...

    assert policy_of(endpoint) is None and policy_kind(endpoint) is None
    assert authz_policy(SELF)(endpoint) is endpoint
    assert policy_of(endpoint) is SELF and policy_kind(endpoint) == "SELF"
    with pytest.raises(TypeError, match="RoutePolicy"):
        authz_policy("SELF")  # type: ignore[arg-type]
