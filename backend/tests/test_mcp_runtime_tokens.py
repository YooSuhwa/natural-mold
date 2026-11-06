"""Run tokens for the shared MCP runtime (app/mcp_runtime, plan §7.3).

The claim set here is the contract the runtime verifies (mcp-runtime/tests); the
end-to-end call is in tests/integration/test_mcp_runtime_probe.py.
"""

from __future__ import annotations

import uuid

import jwt
import pytest

from app.config import settings
from app.mcp_runtime import probe, tokens

SECRET = "s" * 40
IDENTITY = tokens.RunIdentity(
    actor_id=uuid.uuid4(),
    run_id=uuid.uuid4(),
    tenant_id=uuid.uuid4(),
    org_id=uuid.uuid4(),
    agent_id=uuid.uuid4(),
)


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mcp_runtime_token_secret", SECRET)


def test_a_token_carries_the_run_and_is_bound_to_one_server_for_five_minutes() -> None:
    token = tokens.issue("openapi/abc", IDENTITY)

    claims = jwt.decode(
        token, SECRET, algorithms=["HS256"], audience="moldy-mcp-runtime", issuer="moldy-backend"
    )

    assert claims["server"] == "openapi/abc"
    assert (claims["sub"], claims["run_id"]) == (str(IDENTITY.actor_id), str(IDENTITY.run_id))
    assert (claims["tenant_id"], claims["org_id"]) == (
        str(IDENTITY.tenant_id),
        str(IDENTITY.org_id),
    )
    assert claims["agent_id"] == str(IDENTITY.agent_id)
    assert claims["exp"] - claims["iat"] == 300
    assert tokens.issue("openapi/abc", IDENTITY) != token  # each has its own id


def test_the_session_secret_is_never_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mcp_runtime_token_secret", "")
    monkeypatch.setattr(settings, "jwt_secret", "j" * 64)

    with pytest.raises(tokens.RuntimeTokenUnavailable):
        tokens.issue("openapi/abc", IDENTITY)


@pytest.mark.parametrize("lifetime", [0, 301])
def test_lifetime_is_bounded(lifetime: int) -> None:
    with pytest.raises(ValueError, match="1-300"):
        tokens.issue("openapi/abc", IDENTITY, lifetime_seconds=lifetime)


async def test_an_unconfigured_runtime_is_reported_not_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "mcp_runtime_url", "")

    health = await probe.check_runtime()

    assert (health.configured, health.ok) == (False, False)
