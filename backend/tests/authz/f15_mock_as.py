"""Mock MCP authorization server for F-15 tests (plan 11.4.1)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import mcp_oauth_discovery
from app.credentials.creation import CredentialCreation
from app.models.credential import Credential
from app.models.user import User
from tests.authz.unit_scope_fixture import create_test_credential, unit_context
from tests.conftest import TEST_USER_ID

ISSUER = "https://as.example"
SERVER_URL = "HTTPS://MCP.Example/mcp/"
RESOURCE = "https://mcp.example/mcp"


@dataclass
class MockAuthorizationServer:
    registration: bool = True
    cimd: bool = False
    iss_supported: bool = True
    issuer: str | None = ISSUER
    token_requests: list[dict[str, str]] = field(default_factory=list)
    registrations: list[dict[str, Any]] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "issuer": self.issuer,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": ["read"],
            "authorization_response_iss_parameter_supported": self.iss_supported,
            "client_id_metadata_document_supported": self.cimd,
        }
        if self.registration:
            body["registration_endpoint"] = f"{ISSUER}/register"
        return body

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://mcp.example/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(200, json={"authorization_servers": [ISSUER]})
        if url == f"{ISSUER}/.well-known/oauth-authorization-server":
            return httpx.Response(200, json=self.metadata())
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return httpx.Response(404)
        if url == f"{ISSUER}/register" and self.registration:
            self.registrations.append(json.loads(request.content))
            return httpx.Response(201, json={"client_id": "dcr-client"})
        if url == f"{ISSUER}/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_requests.append(form)
            return httpx.Response(
                200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 60}
            )
        return httpx.Response(404)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = httpx.MockTransport(self.handler)
        monkeypatch.setattr(
            mcp_oauth_discovery, "new_http_client", lambda: httpx.AsyncClient(transport=transport)
        )


async def ensure_user(db: AsyncSession) -> None:
    if (await db.execute(select(User).where(User.id == TEST_USER_ID))).scalar_one_or_none():
        return
    db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test User", is_super_user=True))
    await db.commit()


async def mcp_credential(db: AsyncSession, **data: Any) -> Credential:
    payload = {"server_url": SERVER_URL, "use_dynamic_client_registration": True, **data}
    cred = await create_test_credential(
        db,
        CredentialCreation(definition_key="mcp_oauth2", name="MCP", data=payload),
        unit_context(TEST_USER_ID),
    )
    await db.commit()
    return cred


async def start(
    client: AsyncClient, cred: Credential, scope: str | None = None
) -> tuple[httpx.Response, dict[str, str]]:
    body = {"scope": scope} if scope else None
    response = await client.post(f"/api/oauth2-credential/auth/{cred.id}", json=body)
    if response.status_code != 200:
        return response, {}
    query = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    return response, {k: v[0] for k, v in query.items()}
