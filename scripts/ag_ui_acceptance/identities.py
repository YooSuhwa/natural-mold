"""Provision isolated AG-UI test accounts using the same cookie login as the browser."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx


@dataclass(frozen=True)
class IdentityFixture:
    """Keep credentials in process memory; only the evidence dictionary can be exported."""

    environment: dict[str, str] = field(repr=False)
    evidence: dict[str, Any]


def _loopback_host(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "http"
        or parts.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parts.port is None
        or parts.username is not None
        or parts.password is not None
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        raise ValueError("use isolated loopback API URLs")
    return parts.hostname


def _me(client: httpx.Client) -> tuple[dict[str, Any], dict[str, Any]]:
    me = client.get("/api/auth/me").raise_for_status().json()
    memberships = client.get("/api/me/organizations").raise_for_status().json()
    active = [row for row in memberships if row["is_active"]]
    if len(active) != 1 or active[0]["tenant_status"] != "active":
        raise ValueError("fixture account did not resolve to one active organization")
    if active[0]["capabilities_enforced"] is not True:
        raise ValueError("organization capability enforcement must be active on both workers")
    return me, active[0]


def _login(
    client: httpx.Client, email: str, password: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = client.post("/api/auth/login", json={"email": email, "password": password})
    result.raise_for_status()
    csrf = result.json()["csrf_token"]
    if not csrf or csrf != client.cookies.get("moldy_csrf") or not client.cookies.get("moldy_at"):
        raise ValueError("fixture did not receive an authenticated cookie + CSRF pair")
    client.headers["X-CSRF-Token"] = csrf
    me, org = _me(client)
    if me["id"] != result.json()["user"]["id"] or me["email"] != email:
        raise ValueError("cookie identifies a different user")
    return me, org


def provision_identities(
    primary_url: str, other_url: str, seed_email: str, seed_password: str
) -> IdentityFixture:
    """Verify the seeded platform operator and register a separate real, ordinary account."""
    if _loopback_host(primary_url) != _loopback_host(other_url) or (
        urlsplit(primary_url).port == urlsplit(other_url).port
    ):
        raise ValueError("provide two worker ports on the same loopback host")
    with (
        httpx.Client(base_url=primary_url, timeout=20.0, trust_env=False) as primary,
        httpx.Client(base_url=other_url, timeout=20.0, trust_env=False) as other,
    ):
        primary_user, primary_org = _login(primary, seed_email, seed_password)
        if (
            primary_user["is_super_user"] is not True
            or primary_org["id"] != str(uuid5(NAMESPACE_URL, "moldy/e2e-organization"))
            or primary_org["tenant_id"] != str(uuid5(NAMESPACE_URL, "moldy/e2e-tenant"))
        ):
            raise ValueError("operator is not in the reserved E2E fixture company")
        settings = primary.get("/api/system-llm-settings").raise_for_status().json()
        text_primary = next((row for row in settings if row["role"] == "text_primary"), {})
        if (
            text_primary.get("provider") != "e2e_scripted"
            or text_primary.get("model_name") != "document-artifact-scripted"
            or text_primary.get("configured") is not True
        ):
            raise ValueError("text_primary must use the seeded scripted model")

        # Registration selects the new tenant's organization. Log in again independently so
        # the acceptance fixture proves that this choice survives a normal browser sign-in.
        other_email = f"agui-outsider-{uuid4().hex}@moldy.dev"
        other_password = secrets.token_urlsafe(32)
        created = other.post(
            "/api/auth/register",
            json={
                "email": other_email,
                "password": other_password,
                "display_name": "AG-UI acceptance reader",
            },
        )
        created.raise_for_status()
        registered_user = created.json()["user"]["id"]
        other.cookies.clear()
        other_user, other_org = _login(other, other_email, other_password)
        if (
            other_user["id"] != registered_user
            or other_user["is_super_user"] is not False
            or other_org["tenant_kind"] != "personal"
            or primary_org["id"] == other_org["id"]
            or primary_org["tenant_id"] == other_org["tenant_id"]
            or primary_user["id"] == other_user["id"]
        ):
            raise ValueError("fixtures require distinct users, organizations and companies")
        if other.cookies["moldy_at"] == primary.cookies["moldy_at"]:
            raise ValueError("two independent accounts received the same access cookie")
        operator_only = other.get("/api/system-llm-settings")
        if operator_only.status_code != 403:
            raise ValueError("ordinary reader must not have platform operator access")
        # Exercise the privacy boundary on each API process with the same authenticated jar.
        worker_decisions: list[dict[str, int | str]] = []
        for label, url in (("primary", primary_url), ("secondary", other_url)):
            target = f"{url}/api/me/active-org"
            own = other.post(target, json={"org_id": other_org["id"]})
            own.raise_for_status()
            if own.json()["org_id"] != other_org["id"]:
                raise ValueError("active organization was not selected")
            foreign = other.post(target, json={"org_id": primary_org["id"]})
            absent = other.post(target, json={"org_id": str(uuid4())})
            if (
                foreign.status_code != 404
                or foreign.json() != absent.json()
                or absent.status_code != 404
            ):
                raise ValueError("foreign organization differs from an absent organization")
            actual = other.get(f"{url}/api/auth/me").raise_for_status().json()
            if actual["id"] != other_user["id"]:
                raise ValueError("secondary identity did not survive a worker change")
            worker_decisions.append(
                {
                    "worker": label,
                    "own_status": own.status_code,
                    "foreign_and_absent_status": 404,
                }
            )

    variables: dict[str, str] = {
        "MOLDY_AGUI_OTHER_EMAIL": other_email,
        "MOLDY_AGUI_OTHER_PASSWORD": other_password,
        "MOLDY_AGUI_PRIMARY_EMAIL": seed_email,
        "MOLDY_AGUI_PRIMARY_PASSWORD": seed_password,
    }
    subjects: dict[str, dict[str, str | bool]] = {}
    for tag, user, org in (
        ("PRIMARY", primary_user, primary_org),
        ("OTHER", other_user, other_org),
    ):
        ids = {
            "user_id": str(UUID(user["id"])),
            "org_id": str(UUID(org["id"])),
            "tenant_id": str(UUID(org["tenant_id"])),
        }
        variables.update({f"MOLDY_AGUI_{tag}_{key.upper()}": value for key, value in ids.items()})
        subjects[tag.lower()] = {**ids, "operator": user["is_super_user"]}
    return IdentityFixture(
        variables,
        {
            "subjects": subjects,
            "distinct_companies": True,
            "ordinary_reader_operator_route_status": operator_only.status_code,
            "organization_capabilities_enforced": True,
            "text_primary": {
                "provider": text_primary["provider"],
                "model": text_primary["model_name"],
            },
            "organization_results": worker_decisions,
        },
    )
