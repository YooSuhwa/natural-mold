# MCP OAuth 2.1 Atlassian Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement first-class MCP OAuth 2.1 support in Moldy so a user can add the official Atlassian Rovo MCP server, open an Atlassian login/consent window, complete login manually, store OAuth tokens securely, discover MCP tools, and verify real Jira/Confluence document access.

**Architecture:** Keep Moldy's existing `credentials` + `mcp_servers.credential_id` model. Add a proper MCP OAuth client layer around credential OAuth start/callback, persistent OAuth state, PKCE, protected resource metadata discovery, optional Dynamic Client Registration, token refresh persistence, and automatic MCP `Authorization` header injection. Reuse n8n's OAuth client patterns conceptually, but do not copy n8n's MCP OAuth server module because Moldy is the MCP client in this flow.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, httpx, MCP Python SDK, Next.js 16, React 19, TanStack Query, Playwright headed/manual E2E, Atlassian Rovo MCP OAuth 2.1.

---

## Why This Exists

Moldy currently has MCP server registration, credential storage, and OAuth-related hooks, but the existing `mcp_oauth2` definition is not enough for Atlassian's OAuth 2.1 MCP flow.

Current gaps:

- `backend/app/credentials/definitions/mcp_oauth2.py` is effectively a client-credentials/refresh-token helper and does not exchange an authorization code with PKCE.
- `backend/app/routers/credentials.py` has OAuth start/callback routes, but uses in-memory state and assumes the credential already has `client_id` and `authorization_url`.
- `backend/app/mcp/discovery.py` decrypts MCP credentials but does not refresh and persist expired OAuth tokens.
- `backend/app/mcp/client.py` interpolates static MCP headers, but does not automatically apply a credential definition's `authenticate` headers.
- `backend/app/data/mcp_server_registry.json` currently points Atlassian at `https://mcp.atlassian.com/v1/sse` with `http_bearer`. Atlassian's current docs say `/v1/sse` is unsupported after June 30, 2026 and recommend `/mcp`, specifically `https://mcp.atlassian.com/v1/mcp/authv2`, for custom clients.

This plan fixes those gaps without changing the core ownership model.

## Source Basis

### Moldy Source Map

- `backend/app/credentials/definitions/mcp_oauth2.py`: current MCP OAuth credential definition.
- `backend/app/credentials/oauth2_base.py`: token expiry and generic refresh delegation.
- `backend/app/routers/credentials.py`: `/api/oauth2-credential/auth/{id}` and `/api/oauth2-credential/callback`.
- `backend/app/credentials/service.py`: encrypted credential create/update.
- `backend/app/mcp/client.py`: MCP connect/list helpers and header construction.
- `backend/app/mcp/discovery.py`: MCP test/discover/upsert.
- `backend/app/services/chat_service.py`: builds runtime MCP transport headers for agents.
- `backend/app/services/health_check.py`: scheduler health probes for MCP servers.
- `backend/app/data/mcp_server_registry.json`: curated MCP registry entries.
- `frontend/src/components/mcp/mcp-server-wizard.tsx`: MCP wizard credential picker/test flow.
- `frontend/src/components/credential/credential-detail-dialog.tsx`: current OAuth reauthorize button.
- `frontend/src/components/credential/credential-create-modal.tsx`: credential creation modal.
- `frontend/src/components/credential/credential-picker.tsx`: credential selection in MCP wizard.
- `frontend/src/lib/api/credentials.ts`: OAuth start API client.
- `frontend/e2e/mcp-registry.spec.ts`: existing registry wizard E2E pattern.
- `frontend/e2e/mcp-server-wizard.spec.ts`: existing MCP wizard E2E pattern.

### n8n Reference Source Map

Reference repo: `/Users/chester/dev/ref/n8n`

- `/Users/chester/dev/ref/n8n/packages/nodes-base/credentials/OAuth2Api.credentials.ts`
  - Generic OAuth2 credential fields: grant type, server URL, auth URL, token URL, client ID, client secret, scope, token-expired status code.
- `/Users/chester/dev/ref/n8n/packages/@n8n/nodes-langchain/credentials/McpOAuth2Api.credentials.ts`
  - MCP OAuth2 credential extends the generic OAuth2 credential and defaults Dynamic Client Registration to enabled.
- `/Users/chester/dev/ref/n8n/packages/cli/src/oauth/oauth.service.ts`
  - Protected Resource Metadata discovery.
  - Authorization Server Metadata discovery.
  - Dynamic Client Registration.
  - Grant/authentication selection.
  - PKCE code challenge generation.
  - Encrypted CSRF state.
- `/Users/chester/dev/ref/n8n/packages/cli/src/controllers/oauth/oauth2-credential.controller.ts`
  - Callback token exchange.
  - PKCE `code_verifier` handling.
  - Refresh token preservation when a provider omits refresh token on later reconnects.
- `/Users/chester/dev/ref/n8n/packages/core/src/execution-engine/node-execution-context/utils/request-helper-functions.ts`
  - Refresh expired token, persist it, retry request.

Do not copy:

- `/Users/chester/dev/ref/n8n/packages/cli/src/modules/mcp/*`
  - This is n8n acting as an MCP OAuth server/provider. Moldy needs a client for Atlassian's MCP server, so only use it for conceptual validation.

### External References

- Atlassian OAuth 2.1 for Rovo MCP: https://support.atlassian.com/atlassian-rovo-mcp-server/docs/configuring-oauth-2-1/
- Atlassian auth methods: https://support.atlassian.com/atlassian-rovo-mcp-server/docs/authentication-and-authorization/
- Atlassian getting started: https://support.atlassian.com/atlassian-rovo-mcp-server/docs/getting-started-with-the-atlassian-remote-mcp-server/
- MCP authorization spec: https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization

Key external requirements from those docs:

- OAuth 2.1 is the primary interactive auth mechanism for Atlassian Rovo MCP.
- API token auth is optional and only available if the Atlassian organization admin enables it.
- `/v1/sse` is not supported after June 30, 2026.
- Current custom-client recommendation is `https://mcp.atlassian.com/v1/mcp/authv2`.
- MCP clients must send `Authorization: Bearer <access_token>`.
- A real browser login/consent flow is required for interactive OAuth.

## Product Requirements

- A user can select "Atlassian Rovo" from the MCP registry.
- Moldy creates or reuses an `mcp_oauth2` credential for that user.
- The MCP wizard shows a "Connect Atlassian" action when the selected registry entry requires OAuth.
- Clicking the action opens a browser popup/window pointed at Atlassian's OAuth login/consent page.
- The user logs in manually. The E2E test must not type or store the user's Atlassian password.
- After the OAuth callback, Moldy stores encrypted token data on the credential and marks it `active`.
- The popup closes or shows a completion page that tells the opener to refresh credential state.
- MCP test/discover uses a fresh token and automatically injects `Authorization: Bearer <access_token>`.
- Runtime agent MCP tool loading uses the same token-refresh/header-injection path.
- A headed manual E2E flow reaches the Atlassian login/consent page, pauses for the user to complete login, then verifies real Jira/Confluence access with a known query.

## Non-Goals

- Do not automate Atlassian username/password entry.
- Do not store Atlassian passwords.
- Do not replace Moldy's `credentials` table.
- Do not create a separate Atlassian-only database model.
- Do not depend on API-token fallback for the main acceptance path.
- Do not expose a general unaudited "invoke arbitrary MCP tool" product endpoint unless it is explicitly scoped, audited, and gated.

## Data and Token Shape

Store OAuth token/client data inside `credentials.data_encrypted`, not in plaintext columns.

Recommended `mcp_oauth2` payload:

```json
{
  "server_url": "https://mcp.atlassian.com/v1/mcp/authv2",
  "use_dynamic_client_registration": true,
  "auth_url": "https://...",
  "access_token_url": "https://...",
  "registration_url": "https://...",
  "client_id": "dynamic-or-static-client-id",
  "client_secret": "optional-dynamic-client-secret",
  "scope": "space separated scopes when advertised",
  "grant_type": "pkce",
  "authentication": "none",
  "code_verifier": "temporary verifier removed after callback",
  "token_type": "Bearer",
  "access_token": "encrypted",
  "refresh_token": "encrypted",
  "expires_at": 1780000000,
  "account_identifier": "email or Atlassian account id when available",
  "oauth_connected_at": "2026-06-08T00:00:00Z"
}
```

Persistent OAuth state table:

```text
credential_oauth_states
  id uuid pk
  state_hash string unique
  credential_id uuid fk credentials.id
  user_id uuid fk users.id
  redirect_uri string
  code_verifier string nullable
  nonce string nullable
  origin string
  return_to string nullable
  metadata_json json
  consumed_at datetime nullable
  expires_at datetime
  created_at datetime
```

State values sent to the provider should be random opaque tokens. Store only a SHA-256 hash in the DB.

## File Structure

Create:

- `backend/app/models/credential_oauth_state.py`
  - ORM model for short-lived OAuth state.
- `backend/alembic/versions/m60_credential_oauth_states.py`
  - Migration for persistent OAuth state.
- `backend/app/credentials/mcp_oauth_client.py`
  - MCP OAuth 2.1 client helpers: URL validation, PRM discovery, auth server discovery, DCR, PKCE, auth URL, token exchange, refresh.
- `backend/app/mcp/auth.py`
  - Runtime MCP credential resolver: decrypt, refresh with row lock, persist, build auth headers.
- `backend/app/mcp/invocation.py`
  - One-shot MCP tool invocation helper for verification scripts and future debug UI.
- `backend/scripts/verify_atlassian_mcp_access.py`
  - Manual verification script that calls an Atlassian MCP search/read tool after OAuth login.
- `backend/tests/test_mcp_oauth_client.py`
  - Unit tests for discovery, DCR, PKCE, auth URL, code exchange, refresh.
- `backend/tests/test_credential_oauth_state.py`
  - Unit tests for persistent state create/consume/expiry.
- `backend/tests/test_mcp_auth.py`
  - Unit tests for token refresh persistence and header injection.
- `frontend/e2e/manual-atlassian-oauth.spec.ts`
  - Headed/manual E2E that opens Atlassian login and verifies access after the user completes login.

Modify:

- `backend/app/models/__init__.py`
  - Import/export `CredentialOAuthState`.
- `backend/app/credentials/definitions/mcp_oauth2.py`
  - Replace current client-credentials-only behavior with MCP OAuth 2.1 flow support.
- `backend/app/credentials/service.py`
  - Preserve hidden OAuth token fields when a user edits non-token credential fields.
- `backend/app/routers/credentials.py`
  - Use persistent OAuth state, dynamic discovery, PKCE, HTML/popup callback response.
- `backend/app/mcp/client.py`
  - Support already-resolved auth headers from `app.mcp.auth`; keep interpolation.
- `backend/app/mcp/discovery.py`
  - Use `resolve_mcp_auth` instead of raw decrypt.
- `backend/app/services/chat_service.py`
  - Use `resolve_mcp_auth` for runtime MCP tools.
- `backend/app/services/health_check.py`
  - Use `resolve_mcp_auth` for scheduled probes.
- `backend/app/data/mcp_server_registry.json`
  - Replace Atlassian `jira` entry with `atlassian-rovo` using `mcp_oauth2`.
- `backend/app/schemas/mcp.py`
  - Add optional registry auth metadata fields if needed by the frontend.
- `frontend/src/components/mcp/mcp-server-wizard.tsx`
  - Add "Connect Atlassian" credential create/connect path.
- `frontend/src/components/credential/credential-create-modal.tsx`
  - Support initial definition and initial data for one-click MCP credential creation if not already present.
- `frontend/src/components/credential/credential-picker.tsx`
  - Surface connected/auth_needed state clearly.
- `frontend/src/lib/api/credentials.ts`
  - Add OAuth popup callback message handling only in components; API shape can stay the same unless callback URL changes.
- `frontend/src/lib/api/mcp.ts`, `frontend/src/lib/types/mcp.ts`
  - Include registry auth metadata if backend returns it.
- `frontend/messages/ko.json`, `frontend/messages/en.json`
  - Add all new user-facing strings.

## Implementation Tasks

### Task 1: Add Persistent OAuth State

**Files:**

- Create: `backend/app/models/credential_oauth_state.py`
- Create: `backend/alembic/versions/m60_credential_oauth_states.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_credential_oauth_state.py`

- [ ] **Step 1: Write failing tests for state lifecycle**

Create `backend/tests/test_credential_oauth_state.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.credential_oauth_state import CredentialOAuthState
from app.models.user import User
from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
async def _user(db: AsyncSession) -> None:
    existing = await db.execute(select(User).where(User.id == TEST_USER_ID))
    if existing.scalar_one_or_none() is None:
        db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test User"))
        await db.commit()


@pytest.mark.asyncio
async def test_credential_oauth_state_can_be_created_and_consumed(db: AsyncSession) -> None:
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Atlassian",
        data={"server_url": "https://mcp.atlassian.com/v1/mcp/authv2"},
    )
    state = CredentialOAuthState(
        state_hash="hash-1",
        credential_id=cred.id,
        user_id=TEST_USER_ID,
        redirect_uri="http://localhost:8001/api/oauth2-credential/callback",
        code_verifier="verifier",
        origin="credential",
        metadata_json={"provider": "atlassian"},
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=10),
    )
    db.add(state)
    await db.commit()

    row = (
        await db.execute(
            select(CredentialOAuthState).where(CredentialOAuthState.state_hash == "hash-1")
        )
    ).scalar_one()
    assert row.consumed_at is None
    row.consumed_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()

    consumed = (
        await db.execute(
            select(CredentialOAuthState).where(CredentialOAuthState.state_hash == "hash-1")
        )
    ).scalar_one()
    assert consumed.consumed_at is not None
```

- [ ] **Step 2: Run the test and confirm it fails**

Run:

```bash
cd backend
uv run pytest tests/test_credential_oauth_state.py -q
```

Expected: import failure for `app.models.credential_oauth_state`.

- [ ] **Step 3: Add the ORM model**

Create `backend/app/models/credential_oauth_state.py`:

```python
"""Short-lived OAuth state for credential authorization flows."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CredentialOAuthState(Base):
    __tablename__ = "credential_oauth_states"
    __table_args__ = {"extend_existing": True}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    credential_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    redirect_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    code_verifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    nonce: Mapped[str | None] = mapped_column(String(128), nullable=True)
    origin: Mapped[str] = mapped_column(String(40), nullable=False, default="credential")
    return_to: Mapped[str | None] = mapped_column(String(500), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=dict)
    consumed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
```

- [ ] **Step 4: Register the model**

Modify `backend/app/models/__init__.py`:

```python
from app.models.credential_oauth_state import CredentialOAuthState
```

Add `"CredentialOAuthState"` to `__all__`.

- [ ] **Step 5: Add Alembic migration**

Create `backend/alembic/versions/m60_credential_oauth_states.py`:

```python
"""M60: persistent credential oauth states.

Revision ID: m60_credential_oauth_states
Revises: m59_conversation_artifacts
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m60_credential_oauth_states"
down_revision = "m59_conversation_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "credential_oauth_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("redirect_uri", sa.String(length=500), nullable=False),
        sa.Column("code_verifier", sa.Text(), nullable=True),
        sa.Column("nonce", sa.String(length=128), nullable=True),
        sa.Column("origin", sa.String(length=40), nullable=False),
        sa.Column("return_to", sa.String(length=500), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["credential_id"], ["credentials.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash", name="uq_credential_oauth_states_state_hash"),
    )
    op.create_index(
        "ix_credential_oauth_states_credential_created",
        "credential_oauth_states",
        ["credential_id", "created_at"],
    )
    op.create_index(
        "ix_credential_oauth_states_user_expires",
        "credential_oauth_states",
        ["user_id", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_credential_oauth_states_user_expires", table_name="credential_oauth_states")
    op.drop_index(
        "ix_credential_oauth_states_credential_created",
        table_name="credential_oauth_states",
    )
    op.drop_table("credential_oauth_states")
```

- [ ] **Step 6: Verify**

Run:

```bash
cd backend
uv run pytest tests/test_credential_oauth_state.py -q
```

Expected: pass.

Commit:

```bash
git add backend/app/models/credential_oauth_state.py backend/app/models/__init__.py backend/alembic/versions/m60_credential_oauth_states.py backend/tests/test_credential_oauth_state.py
git commit -m "feat(credentials): persist oauth authorization state"
```

### Task 2: Implement MCP OAuth 2.1 Client Helpers

**Files:**

- Create: `backend/app/credentials/mcp_oauth_client.py`
- Test: `backend/tests/test_mcp_oauth_client.py`

- [ ] **Step 1: Write failing helper tests**

Create `backend/tests/test_mcp_oauth_client.py` with these cases:

```python
from __future__ import annotations

import pytest
import httpx

from app.credentials.mcp_oauth_client import (
    build_authorization_url,
    build_pkce_pair,
    discover_authorization_server_metadata,
    discover_protected_resource_metadata,
    dynamic_client_registration,
    exchange_authorization_code,
    select_grant_type_and_authentication,
    validate_oauth_url,
)


def test_validate_oauth_url_rejects_non_http_protocol() -> None:
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        validate_oauth_url("javascript:alert(1)")


def test_pkce_pair_uses_s256() -> None:
    pair = build_pkce_pair()
    assert pair.code_verifier
    assert pair.code_challenge
    assert pair.code_challenge_method == "S256"
    assert pair.code_verifier != pair.code_challenge


@pytest.mark.asyncio
async def test_discover_protected_resource_metadata_path_specific_first() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if str(request.url).endswith("/.well-known/oauth-protected-resource/v1/mcp/authv2"):
            return httpx.Response(
                200,
                json={"authorization_servers": ["https://id.atlassian.com"]},
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        data = await discover_protected_resource_metadata(
            "https://mcp.atlassian.com/v1/mcp/authv2",
            client=client,
        )

    assert data.authorization_servers == ["https://id.atlassian.com"]
    assert requested[0] == (
        "https://mcp.atlassian.com/.well-known/"
        "oauth-protected-resource/v1/mcp/authv2"
    )


@pytest.mark.asyncio
async def test_discover_authorization_server_metadata_supports_path_insertion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://issuer.example/.well-known/oauth-authorization-server/path":
            return httpx.Response(
                200,
                json={
                    "issuer": "https://issuer.example/path",
                    "authorization_endpoint": "https://issuer.example/authorize",
                    "token_endpoint": "https://issuer.example/token",
                    "registration_endpoint": "https://issuer.example/register",
                    "grant_types_supported": ["authorization_code", "refresh_token"],
                    "token_endpoint_auth_methods_supported": ["none"],
                    "code_challenge_methods_supported": ["S256"],
                    "scopes_supported": ["read:confluence", "read:jira"],
                },
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        data = await discover_authorization_server_metadata(
            "https://issuer.example/path",
            client=client,
        )

    assert data.authorization_endpoint == "https://issuer.example/authorize"
    assert data.token_endpoint == "https://issuer.example/token"
    assert data.registration_endpoint == "https://issuer.example/register"


def test_select_grant_prefers_pkce_when_s256_available() -> None:
    selection = select_grant_type_and_authentication(
        grant_types=["authorization_code", "refresh_token"],
        token_endpoint_auth_methods=["none", "client_secret_basic"],
        code_challenge_methods=["S256"],
    )
    assert selection.grant_type == "pkce"
    assert selection.authentication == "none"


@pytest.mark.asyncio
async def test_dynamic_client_registration_posts_expected_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = request.read().decode()
        return httpx.Response(200, json={"client_id": "cid", "client_secret": "sec"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await dynamic_client_registration(
            registration_endpoint="https://issuer.example/register",
            redirect_uri="http://localhost:8001/api/oauth2-credential/callback",
            client_name="Moldy",
            scope="read:confluence read:jira",
            grant_type="pkce",
            authentication="none",
            client=client,
        )

    assert captured["url"] == "https://issuer.example/register"
    assert result.client_id == "cid"
    assert result.client_secret == "sec"


def test_build_authorization_url_includes_pkce_state_and_scope() -> None:
    url = build_authorization_url(
        authorization_endpoint="https://issuer.example/authorize",
        client_id="cid",
        redirect_uri="http://localhost:8001/api/oauth2-credential/callback",
        state="state-1",
        scope="read:jira",
        code_challenge="challenge",
    )
    assert "response_type=code" in url
    assert "client_id=cid" in url
    assert "state=state-1" in url
    assert "code_challenge=challenge" in url
    assert "code_challenge_method=S256" in url


@pytest.mark.asyncio
async def test_exchange_authorization_code_preserves_refresh_token_when_omitted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "fresh",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        patch = await exchange_authorization_code(
            token_endpoint="https://issuer.example/token",
            code="code-1",
            redirect_uri="http://localhost:8001/api/oauth2-credential/callback",
            client_id="cid",
            client_secret=None,
            authentication="none",
            code_verifier="verifier",
            previous_refresh_token="old-refresh",
            client=client,
        )

    assert patch["access_token"] == "fresh"
    assert patch["refresh_token"] == "old-refresh"
    assert patch["token_type"] == "Bearer"
    assert patch["expires_at"] > 0
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
cd backend
uv run pytest tests/test_mcp_oauth_client.py -q
```

Expected: import failure for `app.credentials.mcp_oauth_client`.

- [ ] **Step 3: Implement the helper module**

Create `backend/app/credentials/mcp_oauth_client.py`.

Required public API:

```python
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode, urlparse

import httpx

GrantType = Literal["pkce", "authorization_code", "client_credentials"]
AuthenticationMethod = Literal["none", "header", "body"]


@dataclass(frozen=True)
class PkcePair:
    code_verifier: str
    code_challenge: str
    code_challenge_method: str = "S256"


@dataclass(frozen=True)
class ProtectedResourceMetadata:
    authorization_servers: list[str]
    raw: dict[str, Any]


@dataclass(frozen=True)
class AuthorizationServerMetadata:
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None
    grant_types_supported: list[str]
    token_endpoint_auth_methods_supported: list[str]
    code_challenge_methods_supported: list[str]
    scopes_supported: list[str]
    raw: dict[str, Any]


@dataclass(frozen=True)
class GrantSelection:
    grant_type: GrantType
    authentication: AuthenticationMethod


@dataclass(frozen=True)
class RegistrationResult:
    client_id: str
    client_secret: str | None
    raw: dict[str, Any]
```

Implementation rules:

- `validate_oauth_url(url)` must accept only `http` and `https`.
- `discover_protected_resource_metadata(resource_url)` must try:
  - `{origin}/.well-known/oauth-protected-resource{path}`
  - `{origin}/.well-known/oauth-protected-resource`
- `discover_authorization_server_metadata(issuer_url)` must try:
  - `{origin}/.well-known/oauth-authorization-server{path}`
  - `{origin}/.well-known/openid-configuration{path}`
  - `{issuer_url}/.well-known/openid-configuration`
- `select_grant_type_and_authentication` must prefer:
  - `pkce` + `none` when `authorization_code` and `S256` are supported.
  - `authorization_code` + `header` when `client_secret_basic` is supported.
  - `authorization_code` + `body` when `client_secret_post` is supported.
  - `client_credentials` only as fallback.
- `dynamic_client_registration` must map Moldy auth names to OAuth names:
  - `pkce` + `none` -> `grant_types=["authorization_code", "refresh_token"]`, `token_endpoint_auth_method="none"`
  - `authorization_code` + `header` -> `client_secret_basic`
  - `authorization_code` + `body` -> `client_secret_post`
- `exchange_authorization_code` must support PKCE and body/header/none token auth.
- If token response omits `refresh_token`, preserve `previous_refresh_token`.
- `expires_at = time.time() + expires_in`, default `expires_in=3600`.

- [ ] **Step 4: Verify**

Run:

```bash
cd backend
uv run pytest tests/test_mcp_oauth_client.py -q
```

Expected: pass.

Commit:

```bash
git add backend/app/credentials/mcp_oauth_client.py backend/tests/test_mcp_oauth_client.py
git commit -m "feat(credentials): add mcp oauth client helpers"
```

### Task 3: Upgrade `mcp_oauth2` Credential Definition

**Files:**

- Modify: `backend/app/credentials/definitions/mcp_oauth2.py`
- Test: `backend/tests/test_mcp_oauth2_definition.py`

- [ ] **Step 1: Write tests for authorization-code and refresh behavior**

Create `backend/tests/test_mcp_oauth2_definition.py`:

```python
from __future__ import annotations

import pytest

from app.credentials.definitions import mcp_oauth2


@pytest.mark.asyncio
async def test_mcp_oauth2_exchanges_authorization_code(monkeypatch) -> None:
    async def fake_exchange_authorization_code(**kwargs):
        assert kwargs["code"] == "auth-code"
        assert kwargs["code_verifier"] == "verifier"
        return {
            "access_token": "fresh",
            "refresh_token": "refresh",
            "expires_at": 9999999999.0,
            "token_type": "Bearer",
        }

    monkeypatch.setattr(
        mcp_oauth2,
        "exchange_authorization_code",
        fake_exchange_authorization_code,
    )

    patch = await mcp_oauth2.definition.pre_authentication(
        {
            "authorization_code": "auth-code",
            "access_token_url": "https://issuer.example/token",
            "client_id": "cid",
            "client_secret": "",
            "authentication": "none",
            "redirect_uri": "http://localhost/callback",
            "code_verifier": "verifier",
            "refresh_token": "old-refresh",
        }
    )

    assert patch["access_token"] == "fresh"
    assert patch["refresh_token"] == "refresh"


@pytest.mark.asyncio
async def test_mcp_oauth2_refresh_preserves_new_token(monkeypatch) -> None:
    async def fake_refresh_access_token(**kwargs):
        assert kwargs["refresh_token"] == "refresh"
        return {
            "access_token": "fresh",
            "refresh_token": "refresh",
            "expires_at": 9999999999.0,
            "token_type": "Bearer",
        }

    monkeypatch.setattr(mcp_oauth2, "refresh_access_token", fake_refresh_access_token)

    patch = await mcp_oauth2.definition.pre_authentication(
        {
            "access_token_url": "https://issuer.example/token",
            "client_id": "cid",
            "client_secret": "",
            "authentication": "none",
            "refresh_token": "refresh",
        }
    )

    assert patch["access_token"] == "fresh"
```

- [ ] **Step 2: Run and confirm failure**

Run:

```bash
cd backend
uv run pytest tests/test_mcp_oauth2_definition.py -q
```

Expected: failure because `mcp_oauth2` does not support authorization code.

- [ ] **Step 3: Modify credential fields**

Update `backend/app/credentials/definitions/mcp_oauth2.py` fields to include:

```python
FieldDef(name="server_url", display_name="MCP Server URL", kind=FieldKind.STRING, required=True)
FieldDef(name="use_dynamic_client_registration", display_name="Use Dynamic Client Registration", kind=FieldKind.TOGGLE, default=True)
FieldDef(name="auth_url", display_name="Authorization URL", kind=FieldKind.STRING, required=False)
FieldDef(name="access_token_url", display_name="Access Token URL", kind=FieldKind.STRING, required=False)
FieldDef(name="client_id", display_name="Client ID", kind=FieldKind.STRING, required=False)
FieldDef(name="client_secret", display_name="Client Secret", kind=FieldKind.PASSWORD, required=False, type_options={"password": True})
FieldDef(name="scope", display_name="Scope", kind=FieldKind.STRING, required=False)
FieldDef(name="grant_type", display_name="Grant Type", kind=FieldKind.SELECT, default="pkce", options=[{"name": "PKCE", "value": "pkce"}, {"name": "Authorization Code", "value": "authorization_code"}, {"name": "Client Credentials", "value": "client_credentials"}])
FieldDef(name="authentication", display_name="Token Endpoint Authentication", kind=FieldKind.SELECT, default="none", options=[{"name": "None", "value": "none"}, {"name": "Header", "value": "header"}, {"name": "Body", "value": "body"}])
FieldDef(name="access_token", display_name="Access Token", kind=FieldKind.PASSWORD, required=False, type_options={"password": True, "expirable": True})
FieldDef(name="refresh_token", display_name="Refresh Token", kind=FieldKind.PASSWORD, required=False, type_options={"password": True})
FieldDef(name="expires_at", display_name="Expires At", kind=FieldKind.NUMBER, required=False)
```

- [ ] **Step 4: Replace `_refresh` behavior**

`_refresh(credentials)` must:

- If `authorization_code` is present, call `exchange_authorization_code`.
- Else if `refresh_token` is present, call `refresh_access_token`.
- Else if `grant_type == "client_credentials"`, call client credentials token endpoint.
- Return only the patch fields.
- Never remove a previous refresh token unless the provider explicitly returns a non-empty new one.
- Remove `code_verifier` from the persisted payload after callback by returning `{"code_verifier": None}` or letting the router delete it.

- [ ] **Step 5: Verify**

Run:

```bash
cd backend
uv run pytest tests/test_mcp_oauth2_definition.py tests/test_oauth2.py -q
```

Expected: pass.

Commit:

```bash
git add backend/app/credentials/definitions/mcp_oauth2.py backend/tests/test_mcp_oauth2_definition.py
git commit -m "feat(credentials): support authorization code mcp oauth"
```

### Task 4: Persist OAuth Start and Callback Correctly

**Files:**

- Modify: `backend/app/routers/credentials.py`
- Test: `backend/tests/test_credentials_oauth_flow.py`

- [ ] **Step 1: Write failing router tests**

Create `backend/tests/test_credentials_oauth_flow.py`:

```python
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.credential_oauth_state import CredentialOAuthState
from app.models.user import User
from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
async def _user(db: AsyncSession) -> None:
    existing = await db.execute(select(User).where(User.id == TEST_USER_ID))
    if existing.scalar_one_or_none() is None:
        db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test User", is_super_user=True))
        await db.commit()


@pytest.mark.asyncio
async def test_oauth_start_creates_persistent_state(
    db: AsyncSession,
    client: AsyncClient,
    monkeypatch,
) -> None:
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Atlassian",
        data={
            "server_url": "https://mcp.atlassian.com/v1/mcp/authv2",
            "auth_url": "https://id.example/authorize",
            "access_token_url": "https://id.example/token",
            "client_id": "cid",
            "scope": "read:jira",
            "grant_type": "pkce",
            "authentication": "none",
        },
    )
    await db.commit()

    response = await client.post(f"/api/oauth2-credential/auth/{cred.id}")
    assert response.status_code == 200
    url = response.json()["authorization_url"]
    assert "https://id.example/authorize" in url
    assert "code_challenge=" in url

    rows = (await db.execute(select(CredentialOAuthState))).scalars().all()
    assert len(rows) == 1
    assert rows[0].credential_id == cred.id
    assert rows[0].code_verifier


@pytest.mark.asyncio
async def test_oauth_callback_consumes_state_and_stores_token(
    db: AsyncSession,
    client: AsyncClient,
    monkeypatch,
) -> None:
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Atlassian",
        data={
            "access_token_url": "https://id.example/token",
            "client_id": "cid",
            "authentication": "none",
        },
    )
    raw_state = "state-raw"
    state_hash = hashlib.sha256(raw_state.encode()).hexdigest()
    db.add(
        CredentialOAuthState(
            state_hash=state_hash,
            credential_id=cred.id,
            user_id=TEST_USER_ID,
            redirect_uri="http://test/api/oauth2-credential/callback",
            code_verifier="verifier",
            origin="credential",
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=10),
        )
    )
    await db.commit()

    async def fake_pre_auth(data):
        assert data["authorization_code"] == "code-1"
        assert data["code_verifier"] == "verifier"
        return {
            "access_token": "fresh",
            "refresh_token": "refresh",
            "expires_at": 9999999999.0,
            "token_type": "Bearer",
        }

    from app.credentials.registry import registry

    definition = registry.require("mcp_oauth2")
    monkeypatch.setattr(definition, "pre_authentication", fake_pre_auth)

    response = await client.get(
        "/api/oauth2-credential/callback",
        params={"code": "code-1", "state": raw_state},
    )

    assert response.status_code == 200
    assert "OAuth" in response.text
    await db.refresh(cred)
    payload = credential_service.decrypt_data(cred.data_encrypted)
    assert payload["access_token"] == "fresh"
    assert payload["refresh_token"] == "refresh"
    assert payload.get("code_verifier") is None
```

- [ ] **Step 2: Run and confirm failure**

Run:

```bash
cd backend
uv run pytest tests/test_credentials_oauth_flow.py -q
```

Expected: failure because `_OAUTH_STATE` is still in-memory and callback does not handle PKCE state persistence.

- [ ] **Step 3: Replace in-memory state with DB state**

In `backend/app/routers/credentials.py`:

- Remove `_OAUTH_STATE` as the source of truth.
- Add helpers:
  - `_hash_oauth_state(raw_state: str) -> str`
  - `_create_oauth_state(...) -> tuple[str, CredentialOAuthState]`
  - `_consume_oauth_state(...) -> CredentialOAuthState`
- On start:
  - Load owned credential.
  - Decrypt payload.
  - If `definition_key == "mcp_oauth2"` and `server_url` is present, call the new discovery/DCR helper before building the auth URL.
  - Generate PKCE verifier/challenge.
  - Persist state hash and code verifier.
  - Save discovered `auth_url`, `access_token_url`, `client_id`, `client_secret`, `scope`, `grant_type`, `authentication` back to the credential before returning.
- On callback:
  - Hash the incoming state and fetch unconsumed, unexpired state row.
  - Mark consumed before token exchange or inside the same transaction with row lock.
  - Add `authorization_code`, `redirect_uri`, and `code_verifier` to decrypted credential payload.
  - Call `definition.pre_authentication`.
  - Merge token patch.
  - Delete transient fields: `authorization_code`, `code_verifier`.
  - Preserve existing `refresh_token` if the patch lacks one.
  - Encrypt and persist.
  - Return an HTML completion page, not raw JSON.

Callback HTML:

```html
<!doctype html>
<html>
  <head><meta charset="utf-8"><title>Moldy OAuth Complete</title></head>
  <body>
    <p>OAuth authorization completed. You can close this window.</p>
    <script>
      if (window.opener) {
        window.opener.postMessage(
          { type: 'moldy.oauth.completed', credentialId: '__CREDENTIAL_ID__' },
          window.location.origin
        );
      }
      window.close();
    </script>
  </body>
</html>
```

Use the real credential id in place of `__CREDENTIAL_ID__`.

- [ ] **Step 4: Verify**

Run:

```bash
cd backend
uv run pytest tests/test_credentials_oauth_flow.py tests/test_mcp_oauth_client.py tests/test_mcp_oauth2_definition.py -q
```

Expected: pass.

Commit:

```bash
git add backend/app/routers/credentials.py backend/tests/test_credentials_oauth_flow.py
git commit -m "feat(credentials): persist mcp oauth authorization flow"
```

### Task 5: Preserve Hidden OAuth Token Data on Credential Update

**Files:**

- Modify: `backend/app/credentials/service.py`
- Test: `backend/tests/test_credentials.py` or `backend/tests/test_credentials_oauth_flow.py`

- [ ] **Step 1: Write failing update-preservation test**

Add to `backend/tests/test_credentials_oauth_flow.py`:

```python
@pytest.mark.asyncio
async def test_update_preserves_oauth_tokens_for_mcp_oauth2(db: AsyncSession) -> None:
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Atlassian",
        data={
            "server_url": "https://mcp.atlassian.com/v1/mcp/authv2",
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_at": 9999999999.0,
            "client_id": "cid",
        },
    )
    await db.commit()

    await credential_service.update(
        db,
        credential=cred,
        actor_user_id=TEST_USER_ID,
        data={"server_url": "https://mcp.atlassian.com/v1/mcp/authv2"},
    )
    await db.commit()
    await db.refresh(cred)

    payload = credential_service.decrypt_data(cred.data_encrypted)
    assert payload["access_token"] == "access"
    assert payload["refresh_token"] == "refresh"
    assert payload["expires_at"] == 9999999999.0
    assert payload["client_id"] == "cid"
```

- [ ] **Step 2: Run and confirm failure**

Run:

```bash
cd backend
uv run pytest tests/test_credentials_oauth_flow.py::test_update_preserves_oauth_tokens_for_mcp_oauth2 -q
```

Expected: failure because `credential_service.update` replaces the entire encrypted payload.

- [ ] **Step 3: Implement preservation**

In `backend/app/credentials/service.py`, add:

```python
OAUTH_PRESERVED_FIELDS = {
    "auth_url",
    "access_token_url",
    "registration_url",
    "client_id",
    "client_secret",
    "grant_type",
    "authentication",
    "scope",
    "access_token",
    "refresh_token",
    "expires_at",
    "token_type",
    "account_identifier",
    "oauth_connected_at",
}
OAUTH_DEFINITIONS = {"mcp_oauth2", "google_workspace_oauth2"}
```

When `data is not None` and `credential.definition_key in OAUTH_DEFINITIONS`:

- Decrypt existing payload.
- Merge existing preserved fields into incoming data when incoming data does not explicitly include that key.
- Encrypt merged data.

Do not preserve `authorization_code`, `code_verifier`, `csrfSecret`, or transient state.

- [ ] **Step 4: Verify**

Run:

```bash
cd backend
uv run pytest tests/test_credentials_oauth_flow.py tests/test_credentials.py -q
```

Expected: pass.

Commit:

```bash
git add backend/app/credentials/service.py backend/tests/test_credentials_oauth_flow.py
git commit -m "fix(credentials): preserve oauth token payload on update"
```

### Task 6: Add MCP Auth Resolver and Automatic Header Injection

**Files:**

- Create: `backend/app/mcp/auth.py`
- Modify: `backend/app/mcp/client.py`
- Modify: `backend/app/mcp/discovery.py`
- Modify: `backend/app/services/chat_service.py`
- Modify: `backend/app/services/health_check.py`
- Test: `backend/tests/test_mcp_auth.py`
- Test: `backend/tests/test_mcp.py`
- Test: `backend/tests/test_chat_integration.py`

- [ ] **Step 1: Write failing tests for auth resolver**

Create `backend/tests/test_mcp_auth.py`:

```python
from __future__ import annotations

import time
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.mcp.auth import resolve_mcp_auth
from app.models.user import User
from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
async def _user(db: AsyncSession) -> None:
    existing = await db.execute(select(User).where(User.id == TEST_USER_ID))
    if existing.scalar_one_or_none() is None:
        db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test User"))
        await db.commit()


@pytest.mark.asyncio
async def test_resolve_mcp_auth_injects_http_bearer_header(db: AsyncSession) -> None:
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="http_bearer",
        name="Bearer",
        data={"token": "T-123"},
    )
    await db.commit()

    resolved = await resolve_mcp_auth(db, credential_id=cred.id, user_id=TEST_USER_ID)

    assert resolved.credentials == {"token": "T-123"}
    assert resolved.headers == {"Authorization": "Bearer T-123"}


@pytest.mark.asyncio
async def test_resolve_mcp_auth_refreshes_expired_oauth_and_persists(
    db: AsyncSession,
    monkeypatch,
) -> None:
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="mcp_oauth2",
        name="Atlassian",
        data={
            "access_token": "old",
            "refresh_token": "refresh",
            "expires_at": time.time() - 100,
            "access_token_url": "https://issuer.example/token",
            "client_id": "cid",
            "authentication": "none",
        },
    )
    await db.commit()

    async def fake_refresh(credentials: dict[str, Any]) -> dict[str, Any]:
        return {
            "access_token": "fresh",
            "refresh_token": credentials["refresh_token"],
            "expires_at": time.time() + 3600,
        }

    from app.credentials.registry import registry

    definition = registry.require("mcp_oauth2")
    monkeypatch.setattr(definition, "pre_authentication", fake_refresh)

    resolved = await resolve_mcp_auth(db, credential_id=cred.id, user_id=TEST_USER_ID)

    assert resolved.headers == {"Authorization": "Bearer fresh"}
    await db.refresh(cred)
    payload = credential_service.decrypt_data(cred.data_encrypted)
    assert payload["access_token"] == "fresh"
```

- [ ] **Step 2: Run and confirm failure**

Run:

```bash
cd backend
uv run pytest tests/test_mcp_auth.py -q
```

Expected: import failure for `app.mcp.auth`.

- [ ] **Step 3: Implement `resolve_mcp_auth`**

Create `backend/app/mcp/auth.py`:

```python
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.credentials.interpolation import resolve_deep
from app.credentials.oauth2_base import is_token_expired, refresh_oauth_token
from app.credentials.registry import registry
from app.models.credential import Credential


@dataclass(frozen=True)
class ResolvedMcpAuth:
    credentials: dict[str, Any] | None
    headers: dict[str, str]


def _definition_headers(definition_key: str, credentials: dict[str, Any]) -> dict[str, str]:
    definition = registry.get(definition_key)
    if definition is None or definition.authenticate is None:
        return {}
    raw_headers = definition.authenticate.properties.get("headers", {})
    if not isinstance(raw_headers, dict):
        return {}
    resolved = resolve_deep(raw_headers, credentials)
    return {str(k): str(v) for k, v in resolved.items() if v is not None}


async def resolve_mcp_auth(
    db: AsyncSession,
    *,
    credential_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    static_headers: dict[str, Any] | None = None,
) -> ResolvedMcpAuth:
    if credential_id is None:
        return ResolvedMcpAuth(credentials=None, headers={})

    stmt = select(Credential).where(Credential.id == credential_id)
    if user_id is not None:
        stmt = stmt.where(Credential.user_id == user_id)
    result = await db.execute(stmt.with_for_update())
    credential = result.scalar_one_or_none()
    if credential is None:
        return ResolvedMcpAuth(credentials=None, headers={})

    data = await credential_service.decrypt_with_external(credential.data_encrypted)
    definition = registry.get(credential.definition_key)

    if definition is not None and definition.pre_authentication is not None:
        if credential.definition_key.endswith("oauth2") and is_token_expired(data):
            data = await refresh_oauth_token(definition, data)
            blob, key_id, field_keys = credential_service.encrypt_data(data)
            credential.data_encrypted = blob
            credential.key_id = key_id
            credential.field_keys = field_keys
            credential.status = "active"
            await credential_service.write_audit_log(
                db,
                credential_id=credential.id,
                actor_user_id=credential.user_id,
                action="refresh",
                source="runtime",
                metadata={"reason": "mcp_auth_resolver"},
            )
            await db.flush()

    headers: dict[str, str] = {}
    if static_headers:
        headers.update({str(k): str(v) for k, v in resolve_deep(static_headers, data).items()})
    headers.update(_definition_headers(credential.definition_key, data))
    return ResolvedMcpAuth(credentials=data, headers=headers)
```

- [ ] **Step 4: Update MCP discovery/test/probe**

In `backend/app/mcp/discovery.py`:

- Replace `_decrypt_credential` with `resolve_mcp_auth`.
- Pass `headers=resolved.headers or server.headers` and `credentials=resolved.credentials`.
- Commit happens in router after test/discover; `resolve_mcp_auth` flushes but does not commit.

In `backend/app/routers/mcp.py` probe path:

- If `payload.credential_id` is present, call `resolve_mcp_auth(db, credential_id=payload.credential_id, user_id=user.id, static_headers=headers)`.
- Pass the merged auth headers to `connect_and_list`.

- [ ] **Step 5: Update runtime MCP tool loading**

In `backend/app/services/chat_service.py`, when building MCP configs:

- Replace raw `decrypt_cached(server.credential)` + `build_headers(...)` with `resolve_mcp_auth`.
- Use `static_headers=dict(server.headers or {})`.
- Set `mcp_transport_headers` to `resolved.headers`.
- Keep `credentials` as `resolved.credentials` for legacy interpolation and audit context.

- [ ] **Step 6: Update health checks**

In `backend/app/services/health_check.py`, before calling `mcp_client.connect_and_list`, resolve credentials with the same `resolve_mcp_auth` helper.

- [ ] **Step 7: Verify all MCP-related tests**

Run:

```bash
cd backend
uv run pytest tests/test_mcp_auth.py tests/test_mcp.py tests/test_chat_integration.py tests/test_health_check.py -q
```

Expected: pass.

Commit:

```bash
git add backend/app/mcp/auth.py backend/app/mcp/discovery.py backend/app/routers/mcp.py backend/app/services/chat_service.py backend/app/services/health_check.py backend/tests/test_mcp_auth.py
git commit -m "feat(mcp): resolve oauth credentials before connecting"
```

### Task 7: Update Atlassian Registry Entry

**Files:**

- Modify: `backend/app/data/mcp_server_registry.json`
- Test: `backend/tests/test_mcp_registry.py`

- [ ] **Step 1: Write registry test**

Add to `backend/tests/test_mcp_registry.py`:

```python
def test_atlassian_rovo_registry_uses_mcp_oauth2() -> None:
    from app.services import mcp_registry as registry

    entry = registry.get_registry_entry("atlassian-rovo")
    assert entry is not None
    assert entry["transport"] == "streamable_http"
    assert entry["url"] == "https://mcp.atlassian.com/v1/mcp/authv2"
    assert entry["credential_definition_key"] == "mcp_oauth2"
```

- [ ] **Step 2: Run and confirm failure**

Run:

```bash
cd backend
uv run pytest tests/test_mcp_registry.py::test_atlassian_rovo_registry_uses_mcp_oauth2 -q
```

Expected: failure because current key is `jira` and uses `http_bearer`.

- [ ] **Step 3: Modify registry JSON**

In `backend/app/data/mcp_server_registry.json`, replace the current `jira` entry with:

```json
"atlassian-rovo": {
  "key": "atlassian-rovo",
  "display_name": "Atlassian Rovo",
  "description": "Access Jira, Confluence, Compass, and Jira Service Management through Atlassian's official Rovo MCP server",
  "icon_id": "jira",
  "transport": "streamable_http",
  "url": "https://mcp.atlassian.com/v1/mcp/authv2",
  "command": null,
  "args": null,
  "env_vars": {},
  "credential_definition_key": "mcp_oauth2",
  "documentation_url": "https://support.atlassian.com/atlassian-rovo-mcp-server/docs/getting-started-with-the-atlassian-remote-mcp-server/"
}
```

If backward compatibility is required, keep a deprecated `jira` alias that points to the same URL and credential type, but the UI should display `atlassian-rovo`.

- [ ] **Step 4: Verify**

Run:

```bash
cd backend
uv run pytest tests/test_mcp_registry.py -q
```

Expected: pass.

Commit:

```bash
git add backend/app/data/mcp_server_registry.json backend/tests/test_mcp_registry.py
git commit -m "feat(mcp): add atlassian rovo oauth registry entry"
```

### Task 8: Add MCP Tool Invocation Helper for Real Access Verification

**Files:**

- Create: `backend/app/mcp/invocation.py`
- Create: `backend/scripts/verify_atlassian_mcp_access.py`
- Test: `backend/tests/test_mcp_invocation.py`

- [ ] **Step 1: Write invocation helper tests**

Create `backend/tests/test_mcp_invocation.py`:

```python
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.mcp.invocation import call_mcp_tool_once


@pytest.mark.asyncio
async def test_call_mcp_tool_once_returns_content() -> None:
    captured: dict[str, Any] = {}

    class _FakeResult:
        content = [{"type": "text", "text": "Confluence page result"}]
        structuredContent = None

    class _FakeSession:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc) -> None:
            return None

        async def initialize(self):
            return object()

        async def call_tool(self, name, arguments=None):
            captured["name"] = name
            captured["arguments"] = arguments
            return _FakeResult()

    def _fake_streamable(url, headers=None):
        captured["url"] = url
        captured["headers"] = headers

        class _Conn:
            async def __aenter__(self):
                return (None, None, None)

            async def __aexit__(self, *_exc):
                return None

        return _Conn()

    with (
        patch("mcp.client.session.ClientSession", _FakeSession),
        patch("mcp.client.streamable_http.streamablehttp_client", _fake_streamable),
    ):
        result = await call_mcp_tool_once(
            transport="streamable_http",
            url="https://mcp.example.com",
            headers={"Authorization": "Bearer token"},
            tool_name="search_confluence",
            arguments={"query": "Moldy"},
        )

    assert result["success"] is True
    assert captured["name"] == "search_confluence"
    assert captured["arguments"] == {"query": "Moldy"}
    assert result["content"][0]["text"] == "Confluence page result"
```

- [ ] **Step 2: Implement invocation helper**

Create `backend/app/mcp/invocation.py`:

```python
from __future__ import annotations

from typing import Any

from app.mcp.client import build_headers


async def call_mcp_tool_once(
    *,
    transport: str,
    url: str | None,
    headers: dict[str, Any] | None,
    tool_name: str,
    arguments: dict[str, Any],
    credentials: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if transport not in {"sse", "streamable_http"}:
        return {"success": False, "error": f"transport '{transport}' is not supported"}
    if not url:
        return {"success": False, "error": "url is required"}

    merged_headers = build_headers(headers, credentials)

    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    try:
        async with (
            streamablehttp_client(url, headers=merged_headers or None) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool(tool_name, arguments=arguments)
            return {
                "success": True,
                "content": getattr(result, "content", None),
                "structured_content": getattr(result, "structuredContent", None),
                "raw": result,
            }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}
```

- [ ] **Step 3: Implement verification script**

Create `backend/scripts/verify_atlassian_mcp_access.py`.

Behavior:

- CLI args:
  - `--server-name`, default `Atlassian Rovo`
  - `--query`, default from `E2E_ATLASSIAN_VERIFY_QUERY`
  - `--cloud-id`, optional from `E2E_ATLASSIAN_CLOUD_ID`
- Load DB session.
- Find the current user's MCP server by name or the latest server with URL containing `mcp.atlassian.com`.
- Call `discovery.discover_tools`.
- Pick a candidate tool:
  - Prefer tool names containing `confluence` and `search`.
  - Else names containing `jira` and `search`.
  - Else names containing `search`.
- Build arguments from tool schema:
  - `query`, `q`, `text`, or `search` gets the query.
  - `cloudId` or `cloud_id` gets `--cloud-id` if required.
  - `limit`, `maxResults`, `max_results` get `1`.
  - Unknown required fields fail with a clear message.
- Call `call_mcp_tool_once`.
- Print JSON result.
- Exit `0` only when the result has non-empty `content` or `structured_content`.

Manual verification command:

```bash
cd backend
E2E_USER_EMAIL=playwright-e2e@moldy.dev \
E2E_ATLASSIAN_VERIFY_QUERY="title or issue key that the logged-in Atlassian user can access" \
uv run python scripts/verify_atlassian_mcp_access.py --server-name "Atlassian Rovo"
```

- [ ] **Step 4: Verify**

Run:

```bash
cd backend
uv run pytest tests/test_mcp_invocation.py -q
```

Expected: pass.

Commit:

```bash
git add backend/app/mcp/invocation.py backend/scripts/verify_atlassian_mcp_access.py backend/tests/test_mcp_invocation.py
git commit -m "feat(mcp): add atlassian access verification helper"
```

### Task 9: Add Frontend OAuth Connect Flow in MCP Wizard

**Files:**

- Modify: `frontend/src/components/mcp/mcp-server-wizard.tsx`
- Modify: `frontend/src/components/credential/credential-create-modal.tsx`
- Modify: `frontend/src/components/credential/credential-picker.tsx`
- Modify: `frontend/src/lib/hooks/use-credential-test.ts`
- Modify: `frontend/messages/ko.json`
- Modify: `frontend/messages/en.json`
- Test: `frontend/tests/components/mcp/mcp-server-wizard.test.tsx`
- Test: `frontend/e2e/mcp-registry.spec.ts`

- [ ] **Step 1: Write UI test for OAuth registry card**

Add a test case to `frontend/tests/components/mcp/mcp-server-wizard.test.tsx` or extend existing setup:

```tsx
it('shows OAuth connect action for Atlassian registry entries', async () => {
  // Mock registry entry with credential_definition_key: 'mcp_oauth2'
  // Mock credential types including mcp_oauth2
  // Mock no existing credentials
  // Open wizard, click Atlassian Rovo card, navigate to auth tab
  // Assert "Atlassian 연결" / "Connect Atlassian" action is visible
})
```

Use the existing test utilities in that file. The assertion text must come from i18n messages, not hardcoded TSX copy.

- [ ] **Step 2: Add OAuth completion listener hook**

In `frontend/src/lib/hooks/use-credential-test.ts`, keep `useStartOAuth2` and add a small hook or helper:

```ts
export function openOAuthPopup(url: string): Window | null {
  return window.open(url, '_blank', 'popup,width=720,height=820,noopener,noreferrer')
}
```

In the component, attach:

```ts
useEffect(() => {
  function onMessage(event: MessageEvent) {
    if (event.origin !== window.location.origin) return
    const data = event.data as { type?: string; credentialId?: string }
    if (data.type !== 'moldy.oauth.completed') return
    queryClient.invalidateQueries({ queryKey: ['credentials'] })
    queryClient.invalidateQueries({ queryKey: ['mcp-servers'] })
  }
  window.addEventListener('message', onMessage)
  return () => window.removeEventListener('message', onMessage)
}, [queryClient])
```

- [ ] **Step 3: Add wizard behavior**

In `frontend/src/components/mcp/mcp-server-wizard.tsx`:

- When selected registry entry has `credential_definition_key === 'mcp_oauth2'`, show:
  - Existing credential picker filtered to `mcp_oauth2`.
  - "새 Atlassian 자격증명 만들기" action if none exists.
  - "Atlassian 연결" action once a credential is selected.
- When creating from registry, prefill credential data:

```ts
{
  server_url: selectedRegistry.url,
  use_dynamic_client_registration: true,
  grant_type: 'pkce',
  authentication: 'none'
}
```

- After OAuth completion message, enable the existing "테스트" button and call probe/discover.

- [ ] **Step 4: Add i18n keys**

Add Korean keys under `mcp.wizard.auth`:

```json
{
  "connectOAuth": "Atlassian 연결",
  "createOAuthCredential": "새 Atlassian 자격증명 만들기",
  "oauthConnected": "OAuth 연결이 완료되었습니다.",
  "oauthWaiting": "열린 창에서 Atlassian 로그인과 권한 동의를 완료하세요.",
  "oauthPopupBlocked": "로그인 창을 열 수 없습니다. 팝업 차단 설정을 확인하세요."
}
```

Add matching English keys:

```json
{
  "connectOAuth": "Connect Atlassian",
  "createOAuthCredential": "Create Atlassian credential",
  "oauthConnected": "OAuth connection completed.",
  "oauthWaiting": "Complete Atlassian login and consent in the opened window.",
  "oauthPopupBlocked": "Could not open the login window. Check your popup blocker."
}
```

- [ ] **Step 5: Verify frontend**

Run:

```bash
cd frontend
pnpm lint:i18n
pnpm lint:design-system
pnpm test -- mcp-server-wizard
PW_SKIP_BACKEND=1 pnpm test:e2e -- mcp-registry.spec.ts --project=chromium
```

Expected: pass.

Commit:

```bash
git add frontend/src/components/mcp/mcp-server-wizard.tsx frontend/src/components/credential/credential-create-modal.tsx frontend/src/components/credential/credential-picker.tsx frontend/src/lib/hooks/use-credential-test.ts frontend/messages/ko.json frontend/messages/en.json frontend/tests/components/mcp/mcp-server-wizard.test.tsx frontend/e2e/mcp-registry.spec.ts
git commit -m "feat(mcp): connect oauth credentials from wizard"
```

### Task 10: Add Manual Atlassian OAuth E2E

**Files:**

- Create: `frontend/e2e/manual-atlassian-oauth.spec.ts`
- Modify: `frontend/playwright.config.ts` only if a longer timeout/env override is required.

- [ ] **Step 1: Create manual E2E spec**

Create `frontend/e2e/manual-atlassian-oauth.spec.ts`:

```ts
import { execFileSync } from 'node:child_process'
import { test, expect } from './fixtures'

const manualEnabled = process.env.E2E_ATLASSIAN_MANUAL === '1'
const verifyQuery = process.env.E2E_ATLASSIAN_VERIFY_QUERY

test.describe.configure({ mode: 'serial', timeout: 900_000 })

test.describe('Manual Atlassian MCP OAuth', () => {
  test.skip(!manualEnabled, 'Set E2E_ATLASSIAN_MANUAL=1 to run the real Atlassian OAuth test')
  test.skip(!verifyQuery, 'Set E2E_ATLASSIAN_VERIFY_QUERY to a known accessible Confluence title or Jira issue key')

  test('opens Atlassian login, waits for user consent, then verifies MCP access', async ({ page }) => {
    await page.goto('/mcp-servers')

    await page
      .getByRole('button', { name: /새 MCP 서버|서버 추가/ })
      .first()
      .click()

    await page.getByTestId('registry-card-atlassian-rovo').click()
    await page.getByRole('button', { name: /인증으로 계속/ }).click()

    const popupPromise = page.waitForEvent('popup')
    await page.getByRole('button', { name: /Atlassian 연결|Connect Atlassian/ }).click()
    const popup = await popupPromise
    await popup.waitForLoadState('domcontentloaded')

    await expect(popup).toHaveURL(/atlassian|id\.atlassian|mcp\.atlassian/i, {
      timeout: 60_000,
    })

    console.log(
      [
        '',
        'ACTION REQUIRED:',
        'Complete Atlassian login and OAuth consent in the popup window.',
        'The test will continue after Moldy receives the OAuth callback.',
        '',
      ].join('\n'),
    )

    await popup.waitForEvent('close', { timeout: 600_000 })

    await expect(page.getByText(/OAuth 연결이 완료되었습니다|OAuth connection completed/i)).toBeVisible({
      timeout: 60_000,
    })

    await page.getByRole('button', { name: /테스트|Test/ }).click()
    await expect(page.getByText(/연결됨|connected|도구 발견됨|tools discovered/i)).toBeVisible({
      timeout: 120_000,
    })

    const output = execFileSync(
      'bash',
      [
        '-lc',
        [
          'cd ../backend',
          'uv run python scripts/verify_atlassian_mcp_access.py',
          '--server-name "Atlassian Rovo"',
          `--query ${JSON.stringify(verifyQuery)}`,
        ].join(' '),
      ],
      {
        cwd: process.cwd(),
        env: process.env,
        encoding: 'utf8',
        timeout: 180_000,
      },
    )

    expect(output).toContain('"success": true')
  })
})
```

If the popup does not close because the backend callback HTML cannot call `window.close()` in the browser configuration, replace `await popup.waitForEvent('close')` with:

```ts
await expect(popup.getByText(/OAuth authorization completed/i)).toBeVisible({ timeout: 600_000 })
await popup.close()
```

- [ ] **Step 2: Run mocked E2E first**

Run:

```bash
cd frontend
PW_SKIP_BACKEND=1 pnpm test:e2e -- mcp-registry.spec.ts --project=chromium
```

Expected: pass.

- [ ] **Step 3: Run real headed/manual Atlassian E2E**

Prerequisites:

- Backend `.env` has valid `DATABASE_URL`, `ENCRYPTION_KEY`, `JWT_SECRET`.
- `bash scripts/worktree-setup.sh` has been run if using a worktree.
- Atlassian Cloud site has Rovo MCP enabled.
- Atlassian organization allows OAuth client domains/redirects for localhost, or an equivalent dev callback domain.
- The user running the test has access to a known Jira issue or Confluence page.
- Set `E2E_ATLASSIAN_VERIFY_QUERY` to a title, keyword, or issue key the user can access.

Run:

```bash
cd frontend
E2E_ATLASSIAN_MANUAL=1 \
E2E_ATLASSIAN_VERIFY_QUERY="KNOWN_CONFLUENCE_PAGE_TITLE_OR_JIRA_ISSUE_KEY" \
E2E_WORKERS=1 \
E2E_TEST_TIMEOUT_MS=900000 \
pnpm test:e2e -- manual-atlassian-oauth.spec.ts --project=chromium --headed
```

Expected:

- Browser opens Moldy.
- User opens the Atlassian OAuth popup from the MCP wizard.
- Popup URL is Atlassian login/consent.
- User manually logs in and grants access.
- Popup closes or shows OAuth completion page.
- Moldy shows OAuth connected state.
- MCP test/discover succeeds.
- `verify_atlassian_mcp_access.py` prints JSON containing `"success": true`.

Commit:

```bash
git add frontend/e2e/manual-atlassian-oauth.spec.ts frontend/playwright.config.ts
git commit -m "test(e2e): add manual atlassian mcp oauth flow"
```

### Task 11: Full Verification Matrix

**Files:**

- No new files unless fixes are required.

- [ ] **Step 1: Backend unit and integration tests**

Run:

```bash
cd backend
uv run pytest tests/test_mcp_oauth_client.py tests/test_mcp_oauth2_definition.py tests/test_credentials_oauth_flow.py tests/test_mcp_auth.py tests/test_mcp_invocation.py tests/test_mcp.py tests/test_chat_integration.py tests/test_health_check.py tests/test_mcp_registry.py -q
```

Expected: pass.

- [ ] **Step 2: Backend lint**

Run:

```bash
cd backend
uv run ruff check .
```

Expected: pass.

- [ ] **Step 3: Frontend lint/build**

Run:

```bash
cd frontend
pnpm lint:i18n
pnpm lint:design-system
pnpm build
```

Expected: pass.

- [ ] **Step 4: Mocked E2E**

Run:

```bash
cd frontend
PW_SKIP_BACKEND=1 pnpm test:e2e -- mcp-registry.spec.ts mcp-server-wizard.spec.ts --project=chromium
```

Expected: pass.

- [ ] **Step 5: Real manual Atlassian E2E**

Run:

```bash
cd frontend
E2E_ATLASSIAN_MANUAL=1 \
E2E_ATLASSIAN_VERIFY_QUERY="KNOWN_CONFLUENCE_PAGE_TITLE_OR_JIRA_ISSUE_KEY" \
E2E_WORKERS=1 \
E2E_TEST_TIMEOUT_MS=900000 \
pnpm test:e2e -- manual-atlassian-oauth.spec.ts --project=chromium --headed
```

Expected: pass after the user manually logs in and grants access.

Commit final verification fixes:

```bash
git status --short
git add <changed-files>
git commit -m "test: verify atlassian mcp oauth integration"
```

## Manual E2E Operator Script

Use this exact checklist for the final acceptance run.

1. Start Postgres:

```bash
docker-compose up -d postgres
```

2. Sync worktree env if needed:

```bash
bash scripts/worktree-setup.sh
```

3. Run migrations:

```bash
cd backend
uv run alembic upgrade head
```

4. Start the headed E2E:

```bash
cd frontend
E2E_ATLASSIAN_MANUAL=1 \
E2E_ATLASSIAN_VERIFY_QUERY="KNOWN_CONFLUENCE_PAGE_TITLE_OR_JIRA_ISSUE_KEY" \
E2E_WORKERS=1 \
E2E_TEST_TIMEOUT_MS=900000 \
pnpm test:e2e -- manual-atlassian-oauth.spec.ts --project=chromium --headed
```

5. When the Atlassian popup appears, the human operator must:

- Log in with an Atlassian account.
- Select/confirm the Atlassian Cloud site if prompted.
- Consent to Jira/Confluence access.
- Wait for the popup to close or show the Moldy OAuth completion page.

6. The test must then:

- Return to the Moldy MCP wizard.
- Show OAuth connected state.
- Run MCP test/discover.
- Run `backend/scripts/verify_atlassian_mcp_access.py`.
- Pass only if the verification script receives non-empty content from an Atlassian MCP tool.

## Security and Reliability Notes

- Store only encrypted OAuth token payload in `credentials.data_encrypted`.
- Store only hashed OAuth state in `credential_oauth_states.state_hash`.
- Never log access tokens, refresh tokens, authorization codes, or code verifiers.
- `authorization_code` and `code_verifier` are transient and must not remain in credential payload after callback.
- Use row locks during token refresh to reduce concurrent refresh races.
- If refresh fails, mark the credential `auth_needed` and the MCP server `auth_needed`.
- Do not let arbitrary static headers override `Authorization` produced by `mcp_oauth2` unless explicitly intended. Credential auth should win for OAuth entries.
- The manual E2E must not automate or record the user's Atlassian password.
- Put screenshots/videos/traces under `output/e2e-captures/<YYYYMMDD>-atlassian-mcp-oauth/` if capture fallback is used.

## Acceptance Criteria

- `mcp_oauth2` supports authorization code + PKCE, refresh token, and client credentials fallback.
- OAuth state survives multi-process backend operation because it is stored in the database.
- Atlassian registry entry uses `mcp_oauth2` and the current `/mcp/authv2` endpoint.
- MCP test/discover auto-injects `Authorization: Bearer <access_token>`.
- Runtime agent MCP loading uses refreshed OAuth tokens.
- Credential edits do not accidentally erase stored OAuth tokens.
- Headed manual Playwright E2E opens an Atlassian login/consent window.
- After human login/consent, Moldy stores the token and discovers Atlassian tools.
- Verification script confirms real Jira/Confluence content access using a known query.
- Backend tests, frontend lint/build, mocked E2E, and manual Atlassian E2E pass.

## Execution Options

Plan complete. Implement with one of these modes:

1. **Subagent-Driven (recommended)**: one fresh subagent per task, review after each task, fastest for backend/frontend/E2E split.
2. **Inline Execution**: execute this plan in the current session using `superpowers:executing-plans`, with checkpoints after backend, frontend, and manual E2E tasks.
