"""H-08 fakes: a mock OIDC IdP (httpx MockTransport + RS256 test key), a fake LDAP
directory, a fake DNS TXT resolver and connection/domain seeds."""

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sso.ldap import LdapEntry, LdapSettings
from app.auth.sso.secret_box import seal_secret
from app.models.organization import Organization
from app.models.organization_identity import SsoConnection, TenantDomain
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID

ISSUER = "https://idp.example.test"
CLIENT_ID = "moldy-client"
CLIENT_SECRET = "s3cr3t-client-value"
BIND_PASSWORD = "bind-pw-value"
DOMAIN = "corp.example"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@dataclass
class MockIdp:
    """Discovery, JWKS and token endpoints; ``authorize`` stands in for the browser step."""

    issuer: str = ISSUER
    discovery_issuer: str | None = None
    claims: dict[str, Any] = field(default_factory=dict)
    token_overrides: dict[str, Any] = field(default_factory=dict)
    sign_with_other_key: bool = False
    codes: dict[str, dict[str, str]] = field(default_factory=dict)
    token_requests: list[dict[str, list[str]]] = field(default_factory=list)
    kid: str = "k1"
    fetched: list[str] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.fetched.append(path)
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": self.discovery_issuer or self.issuer,
                    "authorization_endpoint": f"{self.issuer}/authorize",
                    "token_endpoint": f"{self.issuer}/token",
                    "jwks_uri": f"{self.issuer}/jwks",
                    "token_endpoint_auth_methods_supported": ["client_secret_basic"],
                },
            )
        if path == "/jwks":
            jwk = json.loads(RSAAlgorithm.to_jwk(_KEY.public_key()))
            return httpx.Response(200, json={"keys": [jwk | {"kid": self.kid, "use": "sig"}]})
        if path == "/token":
            return self._token(request)
        return httpx.Response(404)

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        self.token_requests.append(form)
        grant = self.codes.pop(form.get("code", [""])[0], None)
        auth = request.headers.get("authorization", "")
        expected = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        if grant is None or auth != f"Basic {expected}":
            return httpx.Response(400, json={"error": "invalid_grant"})
        verifier = form.get("code_verifier", [""])[0]
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        if challenge != grant["challenge"]:
            return httpx.Response(400, json={"error": "invalid_grant"})
        now = int(time.time())
        claims = (
            {
                "iss": self.issuer,
                "aud": CLIENT_ID,
                "sub": "subject-1",
                "iat": now,
                "exp": now + 300,
                "nonce": grant["nonce"],
                "email": f"alice@{DOMAIN}",
                "email_verified": True,
                "name": "Alice",
            }
            | self.claims
            | self.token_overrides
        )
        key = _OTHER_KEY if self.sign_with_other_key else _KEY
        token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": self.kid})
        return httpx.Response(200, json={"id_token": token, "token_type": "Bearer"})

    def authorize(self, location: str) -> tuple[str, str]:
        """Simulate the IdP login: returns ``(code, state)`` for the callback."""
        query = {k: v[0] for k, v in parse_qs(urlparse(location).query).items()}
        assert query["code_challenge_method"] == "S256"
        code = secrets.token_urlsafe(16)
        self.codes[code] = {"nonce": query["nonce"], "challenge": query["code_challenge"]}
        return code, query["state"]


@dataclass
class FakeDirectory:
    """An in-memory directory keyed by the exact (escaped) search filter."""

    entries: dict[str, tuple[str, LdapEntry]] = field(default_factory=dict)
    searches: list[str] = field(default_factory=list)

    def add(self, uid: str, password: str, *, email: str, groups: tuple[str, ...] = ()) -> None:
        entry = LdapEntry(
            dn=f"uid={uid},dc=corp", subject=f"uuid-{uid}", email=email, name=uid, groups=groups
        )
        self.entries[f"(uid={uid})"] = (password, entry)

    async def authenticate(
        self, config: LdapSettings, user_filter: str, password: str
    ) -> LdapEntry | None:
        assert config.bind_password == BIND_PASSWORD
        assert config.server_url.startswith("ldaps://") or config.start_tls
        self.searches.append(user_filter)
        found = self.entries.get(user_filter)
        if found is None or found[0] != password:
            return None
        return found[1]


@dataclass
class FakeTxt:
    records: dict[str, list[str]] = field(default_factory=dict)

    async def txt(self, domain: str) -> list[str]:
        return self.records.get(domain, [])


async def verified_domain(db: AsyncSession, domain: str = DOMAIN, tenant: UUID = TEST_TENANT_ID):
    db.add(TenantDomain(tenant_id=tenant, domain=domain, txt_token="t", status="verified"))
    await db.commit()


async def make_default_org(db: AsyncSession) -> None:
    org = await db.get(Organization, TEST_ORG_ID)
    assert org is not None
    org.is_default = True
    await db.commit()


async def seed_connection(db: AsyncSession, protocol: str = "oidc", **values: Any) -> UUID:
    connection_id = uuid4()
    common: dict[str, Any] = {"id": connection_id, "tenant_id": TEST_TENANT_ID, "status": "draft"}
    if protocol == "oidc":
        row = SsoConnection(
            **common, protocol="oidc", issuer=ISSUER, client_id=CLIENT_ID, scopes=["openid"]
        )
        secret = CLIENT_SECRET
    else:
        row = SsoConnection(
            **common,
            protocol="ldap",
            issuer=f"ldap:{connection_id}",
            ldap_server_url="ldaps://dir.corp.example",
            ldap_bind_dn="cn=svc,dc=corp",
            ldap_user_base_dn="dc=corp",
            ldap_user_filter="(uid={username})",
        )
        secret = BIND_PASSWORD
    db.add(row)
    await db.flush()
    await seal_secret(db, row, secret)
    status = values.pop("status", "active")
    for key, value in values.items():
        setattr(row, key, value)
    row.status = status
    await db.commit()
    return connection_id
