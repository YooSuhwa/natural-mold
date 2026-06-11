"""Publish-time literal-secret allowlist policy for MCP env_vars/headers/args.

Adversarial security review found two opposite failure modes in the
``is_suspicious_secret_value`` allowlist:

* **False positives** — benign structured config (model names, regions,
  UUIDs, enum constants, idempotency / cache-key headers) was rejected,
  so operators couldn't publish legitimate MCP servers.
* **Bypasses** — secrets wrapped as ``https://h/x?sig=…`` URLs or
  ``application/x-<blob>`` MIME types slipped through, and the gate only
  inspected ``env_vars`` / ``headers`` (not ``args`` / agent ``base_url``).

The policy was reworked to be *structure + entropy* based rather than
length-only, with a narrowed credential-header allowlist and re-scanned
URL/MIME exceptions. This file pins both guards:

* FP guard — structured/benign values pass (unit + publish path),
* security guard — high-entropy opaque secrets and recognizable token
  shapes are rejected across env_vars / headers / args,
* credential placeholders still pass.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.marketplace.mcp_server import build_mcp_server_payload
from app.marketplace.secret_scan import is_suspicious_secret_value
from app.models.mcp_server import McpServer
from app.models.user import User
from tests.conftest import TEST_USER_ID

# ===========================================================================
# FALSE-POSITIVE GUARD — benign config that MUST pass (allow)
# ===========================================================================
#
# These were all rejected by the old length-only policy. Structured
# identifiers (>=2 separators), short config, URLs, MIME types and
# idempotency/cache headers must publish cleanly.

# (value, header_name | None)
BENIGN_CASES: list[tuple[str, str | None]] = [
    ("claude-3-5-sonnet-20241022", None),  # model name
    ("prod-cluster-us-east-1a-replica", None),  # region / zone
    ("550e8400-e29b-41d4-a716-446655440000", None),  # non-secret UUID
    ("FEATURE_FLAG_ENABLED_FOR_ALL", None),  # long enum constant
    ("com.example.production.service.handler", None),  # reverse-DNS namespace
    ("us-west-2", None),
    ("application/json", None),
    ("application/x-www-form-urlencoded", None),  # structured MIME subtype
    ("production", None),
    ("30", None),
    ("https://api.example.com", None),  # bare endpoint URL
    ("@modelcontextprotocol/server-filesystem", None),  # npm-style arg
    ("@playwright/mcp@latest", None),  # scoped npm spec arg
    ("req-12345", "Idempotency-Key"),  # idempotency key is not a secret
    ("home-v2", "X-Cache-Key"),
    ("part-7", "X-Partition-Key"),
    ("user-42", "X-Routing-Key"),
    ("abc-1", "X-Request-Key"),
]

# Documented benign env/header config — must NEVER be flagged.
BENIGN_ENV_VARS = [
    {"LOG_LEVEL": "debug"},
    {"BASE_URL": "https://api.example.com"},
    {"REGION": "us-west-2"},
    {"TIMEOUT": "30"},
    {"NODE_ENV": "production"},
    {"MODEL": "claude-3-5-sonnet-20241022"},
]

BENIGN_HEADERS = [
    {"Content-Type": "application/json"},
    {"Accept": "application/json"},
    {"Idempotency-Key": "req-12345"},
    {"X-Cache-Key": "home-v2"},
]


# ===========================================================================
# SECURITY GUARD — values that MUST be rejected (block)
# ===========================================================================
#
# Realistic high-entropy secrets (not degenerate ``B*40`` runs which carry
# no entropy and are an accepted residual miss). Plus known token shapes
# and URL/MIME-wrapped secrets that previously bypassed the gate.

# Continuous high-entropy opaque blobs (no recognizable prefix).
SECRET_ENV_VARS = [
    {"TOKEN_BLOB": "aB3kLm9Qp5Rs7Tv1Wx4Yz6Cd0Ef2Gh"},  # 30-char entropy run
    {"NODE_OPTION": "deadbeefdeadbeefdeadbeefdeadbeef"},  # 32 hex digest
]

# A synthetic Slack-shaped token assembled at runtime — the contiguous
# literal is split so GitHub push-protection doesn't flag the fixture; the
# scanner still sees the full value, so detection is exercised as before.
_SLACK_TOKEN = "xoxb-" + "1234567890-ABCDEFGHIJKLMNOP"

# Credential-carrying headers with opaque values + a known token shape.
SECRET_HEADERS = [
    {"Authorization": "Bearer aB3kLm9Qp5Rs7Tv1Wx"},  # opaque bearer
    {"X-Api-Key": "aB3kLm9Qp5Rs7Tv1Wx4Yz6Cd"},
    {"Cookie": "session=aB3kLm9Qp5Rs7Tv1Wx4"},
    {"X-Tenant": _SLACK_TOKEN},  # Slack content pattern
]

# URL / MIME-wrapped secrets that previously got an unconditional pass.
SECRET_DISGUISED = [
    "https://h/x?sig=aB3kLm9Qp5Rs7Tv1Wx4Yz",  # signed URL query
    "https://u:aB3kLm9Qp5Rs7@h",  # URL userinfo password
    "application/x-realsecrettoken12345",  # opaque MIME subtype
]

# Known-shape content tokens — always rejected regardless of structure.
SECRET_CONTENT_TOKENS = [
    "sk-abc1234567890ABCDEFghij",
    _SLACK_TOKEN,
    "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
]

# args entries — flag/value command lines carry secrets too.
SECRET_ARGS = ["--api-key", "aB3kLm9Qp5Rs7Tv1Wx4Yz6"]


# ===========================================================================
# Unit policy — ``is_suspicious_secret_value``
# ===========================================================================


class TestSuspiciousValuePolicy:
    @pytest.mark.parametrize(("value", "header"), BENIGN_CASES)
    def test_benign_structured_values_allowed(
        self, value: str, header: str | None
    ) -> None:
        assert not is_suspicious_secret_value(value, header_name=header), (
            value,
            header,
        )

    @pytest.mark.parametrize("mapping", BENIGN_ENV_VARS)
    def test_benign_env_values_allowed(self, mapping: dict) -> None:
        for value in mapping.values():
            assert not is_suspicious_secret_value(value), value

    @pytest.mark.parametrize("mapping", BENIGN_HEADERS)
    def test_benign_header_values_allowed(self, mapping: dict) -> None:
        for name, value in mapping.items():
            assert not is_suspicious_secret_value(value, header_name=name), (
                name,
                value,
            )

    @pytest.mark.parametrize("mapping", SECRET_ENV_VARS)
    def test_opaque_env_values_flagged(self, mapping: dict) -> None:
        for value in mapping.values():
            assert is_suspicious_secret_value(value), value

    @pytest.mark.parametrize("mapping", SECRET_HEADERS)
    def test_secret_header_values_flagged(self, mapping: dict) -> None:
        for name, value in mapping.items():
            assert is_suspicious_secret_value(value, header_name=name), (name, value)

    @pytest.mark.parametrize("value", SECRET_DISGUISED)
    def test_url_mime_wrapped_secrets_flagged(self, value: str) -> None:
        assert is_suspicious_secret_value(value), value

    @pytest.mark.parametrize("value", SECRET_CONTENT_TOKENS)
    def test_known_content_tokens_flagged(self, value: str) -> None:
        # Recognizable token shapes block regardless of header/structure.
        assert is_suspicious_secret_value(value), value

    @pytest.mark.parametrize("value", SECRET_ARGS[1:])
    def test_opaque_arg_value_flagged(self, value: str) -> None:
        assert is_suspicious_secret_value(value), value

    def test_credential_placeholders_allowed(self) -> None:
        assert not is_suspicious_secret_value("={{ $credentials.access_token }}")
        assert not is_suspicious_secret_value(
            "=Bearer {{ $credentials.access_token }}", header_name="Authorization"
        )

    def test_empty_value_allowed(self) -> None:
        assert not is_suspicious_secret_value("")
        assert not is_suspicious_secret_value("   ")

    def test_non_string_allowed(self) -> None:
        assert not is_suspicious_secret_value(None)
        assert not is_suspicious_secret_value(30)


# ===========================================================================
# Builder gate — ``build_mcp_server_payload``
# ===========================================================================


def _server(**overrides) -> McpServer:
    data = {
        "id": uuid.uuid4(),
        "user_id": TEST_USER_ID,
        "name": "Policy MCP",
        "description": "Policy test server",
        "transport": "streamable_http",
        "url": "https://mcp.example.test/mcp",
        "command": None,
        "args": [],
        "env_vars": {},
        "headers": {},
        "credential_id": None,
        "status": "connected",
    }
    data.update(overrides)
    return McpServer(**data)


class TestBuildPayloadGate:
    @pytest.mark.parametrize("env_vars", SECRET_ENV_VARS)
    def test_secret_env_vars_block_payload(self, env_vars: dict) -> None:
        with pytest.raises(AppError) as exc:
            build_mcp_server_payload(
                _server(env_vars=env_vars),
                credential_definition_key=None,
            )
        assert exc.value.code == "MARKETPLACE_SECRET_DETECTED"
        # Actionable hint pointing to interpolation.
        assert "{{$credentials" in exc.value.message

    @pytest.mark.parametrize("headers", SECRET_HEADERS)
    def test_secret_headers_block_payload(self, headers: dict) -> None:
        with pytest.raises(AppError) as exc:
            build_mcp_server_payload(
                _server(headers=headers),
                credential_definition_key=None,
            )
        assert exc.value.code == "MARKETPLACE_SECRET_DETECTED"

    def test_secret_args_block_payload(self) -> None:
        with pytest.raises(AppError) as exc:
            build_mcp_server_payload(
                _server(command="npx", args=list(SECRET_ARGS)),
                credential_definition_key=None,
            )
        assert exc.value.code == "MARKETPLACE_SECRET_DETECTED"
        assert "args[" in exc.value.message

    def test_benign_args_pass_payload(self) -> None:
        # npm-style command line must publish cleanly.
        payload = build_mcp_server_payload(
            _server(
                transport="stdio",
                url=None,
                command="npx",
                args=["-y", "@modelcontextprotocol/server-filesystem"],
            ),
            credential_definition_key=None,
        )
        assert payload["args"] == ["-y", "@modelcontextprotocol/server-filesystem"]

    def test_benign_config_passes_payload(self) -> None:
        merged_env: dict[str, str] = {}
        for mapping in BENIGN_ENV_VARS:
            merged_env.update(mapping)
        merged_headers: dict[str, str] = {}
        for mapping in BENIGN_HEADERS:
            merged_headers.update(mapping)

        payload = build_mcp_server_payload(
            _server(env_vars=merged_env, headers=merged_headers),
            credential_definition_key=None,
        )
        assert payload["env_vars"] == merged_env
        assert payload["headers"] == merged_headers

    def test_placeholder_headers_pass_payload(self) -> None:
        payload = build_mcp_server_payload(
            _server(headers={"Authorization": "=Bearer {{ $credentials.access_token }}"}),
            credential_definition_key="mcp_oauth2",
        )
        assert payload["headers"] == {
            "Authorization": "=Bearer {{ $credentials.access_token }}"
        }


# ===========================================================================
# Publish path — end-to-end rejection
# ===========================================================================


async def _ensure_test_user(db: AsyncSession) -> None:
    user = await db.get(User, TEST_USER_ID)
    if user is None:
        db.add(
            User(
                id=TEST_USER_ID,
                email="test@test.com",
                name="Test User",
                hashed_password="h",
                is_active=True,
                is_super_user=True,
            )
        )
        await db.flush()


@pytest.mark.asyncio
async def test_publish_mcp_server_rejects_literal_secret_env_var(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    server = _server(
        env_vars={"TOKEN_BLOB": "aB3kLm9Qp5Rs7Tv1Wx4Yz6Cd0Ef2Gh"}, headers={}
    )
    db.add(server)
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/from-mcp/{server.id}",
        json={"visibility": "private", "name": "Leaky Env MCP"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "MARKETPLACE_SECRET_DETECTED"


@pytest.mark.asyncio
async def test_publish_mcp_server_rejects_literal_secret_header(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    server = _server(
        headers={"X-Api-Key": "aB3kLm9Qp5Rs7Tv1Wx4Yz6Cd"},
        env_vars={},
    )
    db.add(server)
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/from-mcp/{server.id}",
        json={"visibility": "private", "name": "Leaky Header MCP"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "MARKETPLACE_SECRET_DETECTED"


@pytest.mark.asyncio
async def test_publish_mcp_server_rejects_literal_secret_arg(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    server = _server(
        transport="stdio",
        url=None,
        command="npx",
        args=["--api-key", "aB3kLm9Qp5Rs7Tv1Wx4Yz6"],
        headers={},
        env_vars={},
    )
    db.add(server)
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/from-mcp/{server.id}",
        json={"visibility": "private", "name": "Leaky Args MCP"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "MARKETPLACE_SECRET_DETECTED"


@pytest.mark.asyncio
async def test_publish_mcp_server_allows_benign_config(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _ensure_test_user(db)
    server = _server(
        env_vars={
            "LOG_LEVEL": "debug",
            "REGION": "us-west-2",
            "TIMEOUT": "30",
            "MODEL": "claude-3-5-sonnet-20241022",
        },
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": "req-12345",
        },
    )
    db.add(server)
    await db.commit()

    response = await client.post(
        f"/api/marketplace/items/from-mcp/{server.id}",
        json={"visibility": "private", "name": "Clean Config MCP"},
    )

    assert response.status_code == 201, response.text
