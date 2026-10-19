"""I-08 request rate-limit keys never share a bucket across companies, and Langfuse
traces carry the run's company/organization with no bodies in multi-tenant (plan 8.9)."""

import uuid
from contextlib import nullcontext
from typing import Any

from starlette.requests import Request

from app.agent_runtime.runtime_config import AgentConfig
from app.authz.org_context import OrgContext
from app.config import settings
from app.dependencies import CurrentUser
from app.observability import langfuse as langfuse_obs
from app.rate_limit import caller_key, share_key
from app.routers.agent_readiness import caller_rate_key
from app.routers.org_search import principal_rate_key
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID

OTHER_TENANT = uuid.uuid5(uuid.NAMESPACE_URL, "moldy/i08-rate-other-company")


def _request(**state: object) -> Request:
    request = Request({"type": "http", "headers": [], "client": ("10.0.0.7", 1)})
    for name, value in state.items():
        setattr(request.state, name, value)
    return request


def test_authenticated_keys_are_per_company_and_user() -> None:
    key = caller_key("scope")
    mine = OrgContext(org_id=TEST_ORG_ID, tenant_id=TEST_TENANT_ID, user_id=TEST_USER_ID)
    other = OrgContext(org_id=uuid.uuid4(), tenant_id=OTHER_TENANT, user_id=TEST_USER_ID)
    assert key(_request(org_context=mine)) == f"scope:tenant:{TEST_TENANT_ID}:user:{TEST_USER_ID}"
    assert key(_request(org_context=mine)) != key(_request(org_context=other))
    operator = CurrentUser(id=TEST_USER_ID, email="o@x", name="o", is_super_user=True)
    assert key(_request(current_user=operator)) == f"scope:platform:user:{TEST_USER_ID}"
    assert key(_request()) == "scope:unauthenticated:10.0.0.7"
    for route_key in (caller_rate_key, principal_rate_key):
        assert f"tenant:{TEST_TENANT_ID}:user:{TEST_USER_ID}" in route_key(
            _request(org_context=mine)
        )


def test_public_share_keys_are_per_token_company_and_ip() -> None:
    assert share_key(_request(share_tenant_id=TEST_TENANT_ID)) == (
        f"share:tenant:{TEST_TENANT_ID}:ip:10.0.0.7"
    )
    assert share_key(_request(share_tenant_id=OTHER_TENANT)) != share_key(
        _request(share_tenant_id=TEST_TENANT_ID)
    )
    assert share_key(_request(share_tenant_id=None)) == "share:tenant:unknown:ip:10.0.0.7"


def _cfg() -> AgentConfig:
    return AgentConfig(
        org_context=unit_context(TEST_USER_ID),
        provider="openai",
        model_name="gpt-4o",
        api_key="sk-test",
        base_url=None,
        system_prompt="You are helpful.",
        tools_config=[],
        thread_id="conv-1",
        agent_id="agent-1",
        user_id=str(TEST_USER_ID),
    )


class _Client:
    def __init__(self) -> None:
        self.observations: list[dict[str, Any]] = []

    def create_trace_id(self, seed: str) -> str:
        return seed.replace("-", "").ljust(32, "0")[:32]

    def start_as_current_observation(self, **kwargs: Any):
        self.observations.append(kwargs)
        return nullcontext()


def _context(monkeypatch, client: _Client):
    for name, value in (
        ("langfuse_enabled", True),
        ("langfuse_public_key", "pk-lf-test"),
        ("langfuse_secret_key", "sk-lf-test"),
        ("langfuse_base_url", "https://langfuse.local"),
        ("langfuse_capture_input_output", True),
    ):
        monkeypatch.setattr(settings, name, value, raising=False)
    monkeypatch.setattr(langfuse_obs, "_get_langfuse_client", lambda: client)
    monkeypatch.setattr(langfuse_obs, "_build_callback_handler", lambda *, trace_id: object())
    return langfuse_obs.build_langfuse_run_context(_cfg(), run_id="run-1", source="chat")


def test_traces_carry_the_run_company_and_organization(monkeypatch) -> None:
    ctx = _context(monkeypatch, _Client())
    assert ctx.metadata is not None and ctx.tags is not None
    assert ctx.metadata["moldy_tenant_id"] == str(TEST_TENANT_ID)
    assert ctx.metadata["moldy_org_id"] == str(TEST_ORG_ID)
    assert f"tenant:{TEST_TENANT_ID}" in ctx.tags


def test_multi_tenant_never_records_bodies(monkeypatch) -> None:
    client = _Client()
    ctx = _context(monkeypatch, client)
    with ctx.activate(input_payload={"note": "hello"}):
        pass
    assert client.observations[-1]["input"] == {"note": "hello"}  # single tenant: setting kept
    monkeypatch.setattr(settings, "deployment_mode", "multi_tenant")
    with ctx.activate(input_payload={"note": "hello"}, output_payload="answer"):
        pass
    assert client.observations[-1]["input"] == "[redacted: input/output capture disabled]"
    assert client.observations[-1]["output"] == "[redacted]"
    assert langfuse_obs.redact_payload({"content": "x", "n": "body"}) == {
        "content": "[redacted]",
        "n": "[redacted]",
    }
