"""OIDC Authorization Code + PKCE client (plan 10.10): discovery, token, ID token.

Every check fails closed: the discovery ``issuer`` must equal the configured issuer
exactly, endpoints must be HTTPS, the ID token must be signed with an asymmetric
algorithm by a key from the issuer's JWKS and carry the expected ``iss``/``aud``/
``nonce`` (``azp`` when several audiences). IdP error bodies are never surfaced.
"""

import hmac
import time
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import quote

import httpx

from app.auth.jwt import (
    ExternalIssuerError,
    ExternalTokenError,
    jwks_keys,
    unverified_jwt_header,
    verify_external_jwt,
)
from app.auth.sso.errors import sso_error
from app.auth.sso.http import idp_client
from app.auth.sso.state import LoginTransaction
from app.outbound.destination import OutboundBlockedError

ALGORITHMS: Final = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"}
)
_LEEWAY: Final = 60


@dataclass(frozen=True, slots=True)
class OidcProvider:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    token_auth_methods: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OidcClient:
    client_id: str
    client_secret: str
    redirect_uri: str


_CACHE_TTL: Final = 300.0
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def clear_cache() -> None:
    _cache.clear()


async def _get_json(url: str, failure: str, *, fresh: bool = False) -> dict[str, Any]:
    """GET a JSON document; discovery and JWKS are cached for 5 minutes per URL."""
    cached = _cache.get(url)
    if cached is not None and not fresh and cached[0] > time.monotonic():
        return cached[1]
    try:
        async with idp_client() as client:
            response = await client.get(url, headers={"Accept": "application/json"})
        body = response.json() if response.status_code == 200 else None
    except (httpx.HTTPError, OutboundBlockedError, ValueError) as error:
        raise sso_error(failure) from error
    if not isinstance(body, dict):
        raise sso_error(failure)
    if len(_cache) > 1000:
        _cache.clear()
    _cache[url] = (time.monotonic() + _CACHE_TTL, body)
    return body


def _https(document: dict[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.startswith("https://"):
        raise sso_error("sso_discovery_failed")
    return value


async def discover(issuer: str) -> OidcProvider:
    document = await _get_json(
        issuer.rstrip("/") + "/.well-known/openid-configuration", "sso_discovery_failed"
    )
    if document.get("issuer") != issuer:
        raise sso_error("sso_issuer_mismatch")
    methods = document.get("token_endpoint_auth_methods_supported")
    return OidcProvider(
        issuer=issuer,
        authorization_endpoint=_https(document, "authorization_endpoint"),
        token_endpoint=_https(document, "token_endpoint"),
        jwks_uri=_https(document, "jwks_uri"),
        token_auth_methods=tuple(m for m in methods if isinstance(m, str))
        if isinstance(methods, list)
        else (),
    )


def authorization_url(
    provider: OidcProvider, client: OidcClient, scopes: list[str], tx: LoginTransaction
) -> str:
    params = {
        "response_type": "code",
        "client_id": client.client_id,
        "redirect_uri": client.redirect_uri,
        "scope": " ".join(dict.fromkeys(["openid", *scopes])),
        "state": tx.state,
        "nonce": tx.nonce,
        "code_challenge": tx.challenge,
        "code_challenge_method": "S256",
    }
    return str(httpx.URL(provider.authorization_endpoint).copy_merge_params(params))


async def exchange_code(
    provider: OidcProvider, client: OidcClient, *, code: str, verifier: str
) -> str:
    """Return the raw ID token from the token endpoint."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": client.redirect_uri,
        "code_verifier": verifier,
    }
    basic = "client_secret_basic" in provider.token_auth_methods
    if not basic and "client_secret_post" in provider.token_auth_methods:
        data |= {"client_id": client.client_id, "client_secret": client.client_secret}
    else:  # RFC 6749 2.3.1: Basic with form-encoded id/secret is the default
        basic = True
    auth = httpx.BasicAuth(quote(client.client_id, safe=""), quote(client.client_secret, safe=""))
    try:
        async with idp_client() as http:
            response = await (
                http.post(provider.token_endpoint, data=data, auth=auth)
                if basic
                else http.post(provider.token_endpoint, data=data)
            )
        body = response.json() if response.status_code == 200 else None
    except (httpx.HTTPError, OutboundBlockedError, ValueError) as error:
        raise sso_error("sso_idp_error") from error
    token = body.get("id_token") if isinstance(body, dict) else None
    if not isinstance(token, str):
        raise sso_error("sso_idp_error")
    return token


async def _matching_keys(provider: OidcProvider, kid: object, *, fresh: bool) -> list[Any]:
    document = await _get_json(provider.jwks_uri, "sso_token_invalid", fresh=fresh)
    try:
        keys = jwks_keys(document)
    except ExternalTokenError as error:
        raise sso_error("sso_token_invalid") from error
    return [key for key_id, key in keys if kid is None or key_id == kid]


async def _signing_key(provider: OidcProvider, kid: object) -> Any:
    matches = await _matching_keys(provider, kid, fresh=False)
    if not matches:  # an unknown kid: the IdP may have rotated its keys
        matches = await _matching_keys(provider, kid, fresh=True)
    if len(matches) != 1:
        raise sso_error("sso_token_invalid")
    return matches[0]


async def verify_id_token(
    provider: OidcProvider, token: str, *, client_id: str, nonce: str
) -> dict[str, Any]:
    try:
        header = unverified_jwt_header(token)
    except ExternalTokenError as error:
        raise sso_error("sso_token_invalid") from error
    algorithm = header.get("alg")
    if algorithm not in ALGORITHMS:
        raise sso_error("sso_token_invalid")
    key = await _signing_key(provider, header.get("kid"))
    try:
        claims = verify_external_jwt(
            token,
            key,
            algorithm=str(algorithm),
            audience=client_id,
            issuer=provider.issuer,
            leeway=_LEEWAY,
        )
    except ExternalIssuerError as error:
        raise sso_error("sso_issuer_mismatch") from error
    except ExternalTokenError as error:
        raise sso_error("sso_token_invalid") from error
    if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise sso_error("sso_token_invalid")
    audience = claims.get("aud")
    if isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != client_id:
        raise sso_error("sso_token_invalid")
    return claims
