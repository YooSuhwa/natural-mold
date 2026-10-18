"""I-05 automated security probes (plan 16.6 security checklist, 8.9 rate-limit keys).

Each probe returns ``Finding`` rows. Probes touch only the prober's own resources or
IDs that are either foreign (must be hidden) or nonexistent; mutation IDOR probes
send an empty body and stay off unless the operator opts in.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from typing import Any

import httpx
from i05_http import ACTIVE_ORG_COOKIE, Session, error_code
from i05_report import Finding
from i05_route_inventory import Probe


def _body(method: str) -> dict[str, Any]:
    return {} if method in ("GET", "HEAD", "DELETE") else {"json": {}}


async def idor(session: Session, probes: Sequence[Probe]) -> list[Finding]:
    """Foreign IDs must answer exactly like nonexistent IDs: 404 with the same code."""
    findings: list[Finding] = []
    for probe in probes:
        foreign = await session.call(probe.method, probe.path, **_body(probe.method))
        control = await session.call(probe.method, probe.control_path, **_body(probe.method))
        target = f"{probe.method} {probe.template}"
        detail = (
            f"foreign={foreign.status_code}/{error_code(foreign)} "
            f"control={control.status_code}/{error_code(control)} "
            f"params={list(probe.foreign_params)}"
        )
        same = (foreign.status_code, error_code(foreign)) == (
            control.status_code,
            error_code(control),
        )
        ok = foreign.status_code == 404 and same
        if foreign.is_success:
            detail = "CRITICAL foreign resource answered 2xx; " + detail
        findings.append(Finding("idor.404_parity", target, "pass" if ok else "fail", detail))
    return findings


async def csrf(session: Session, probes: Sequence[Probe]) -> list[Finding]:
    """Mutations without (or with a forged) X-CSRF-Token must get 403 csrf_mismatch."""
    findings: list[Finding] = []
    forged = {"X-CSRF-Token": secrets.token_urlsafe(24)}
    for probe in probes:
        target = f"{probe.method} {probe.template}"
        for label, headers, use_csrf in (("missing", {}, False), ("forged", forged, False)):
            response = await session.call(
                probe.method, probe.path, csrf=use_csrf, headers=headers, **_body(probe.method)
            )
            ok = response.status_code == 403 and error_code(response) == "csrf_mismatch"
            detail = f"{label} header -> {response.status_code}/{error_code(response)}"
            findings.append(Finding(f"csrf.{label}", target, "pass" if ok else "fail", detail))
    return findings


async def forged_org_cookie(session: Session, foreign_org_id: str) -> list[Finding]:
    """A non-member organization in ``moldy_active_org`` must be refused (403)."""
    cookie = f"{ACTIVE_ORG_COOKIE}={foreign_org_id}"
    response = await session.client.get(
        "/api/agents", headers={"Cookie": _cookies(session, cookie)}
    )
    ok = response.status_code == 403
    detail = f"-> {response.status_code}/{error_code(response)}"
    return [Finding("auth.forged_org_cookie", "GET /api/agents", "pass" if ok else "fail", detail)]


def _cookies(session: Session, override: str) -> str:
    name = override.split("=", 1)[0]
    kept = [f"{k}={v}" for k, v in session.client.cookies.items() if k != name]
    return "; ".join([*kept, override])


async def rate_limit_isolation(
    noisy: Session, quiet: Session, *, max_requests: int = 40
) -> list[Finding]:
    """Exhaust company A's principal-search bucket; company B must stay unaffected."""
    target = "GET /api/orgs/{org_id}/principals"
    if not noisy.org_id or not quiet.org_id:
        return [Finding("rate_limit.company_keys", target, "skipped", "personas need org_id")]
    limited = False
    for _ in range(max_requests):
        response = await noisy.call(
            "GET", f"/api/orgs/{noisy.org_id}/principals", params={"q": "i05"}
        )
        if response.status_code == 429:
            limited = True
            break
    if not limited:
        detail = f"no 429 after {max_requests} requests (rate limiting disabled?)"
        return [Finding("rate_limit.company_keys", target, "fail", detail)]
    other = await quiet.call("GET", f"/api/orgs/{quiet.org_id}/principals", params={"q": "i05"})
    ok = other.status_code != 429
    detail = f"A limited; B -> {other.status_code}"
    return [Finding("rate_limit.company_keys", target, "pass" if ok else "fail", detail)]


async def share_links(
    owner: Session,
    anonymous: httpx.AsyncClient,
    conversation_id: str,
    foreign_artifact_id: str | None,
    suspended_token: str | None,
) -> list[Finding]:
    """Public share link company checks on the owner's own conversation (plan 8.9)."""
    out: list[Finding] = []

    def add(check: str, target: str, ok: bool, response: httpx.Response) -> None:
        detail = f"-> {response.status_code}/{error_code(response)}"
        out.append(Finding(check, target, "pass" if ok else "fail", detail))

    created = await owner.call("POST", f"/api/conversations/{conversation_id}/share")
    if created.status_code != 200:
        add("share.create", "POST /api/conversations/{id}/share", False, created)
        return out
    token = str(created.json()["share_token"])
    opened = await anonymous.get(f"/api/shares/{token}")
    add("share.open_public", "GET /api/shares/{token}", opened.status_code == 200, opened)
    if foreign_artifact_id:
        nested = await anonymous.get(f"/api/shares/{token}/artifacts/{foreign_artifact_id}")
        target = "GET /api/shares/{token}/artifacts/{foreign}"
        add("share.nested_idor", target, nested.status_code == 404, nested)
    bogus = await anonymous.get(f"/api/shares/{secrets.token_urlsafe(24)}")
    add("share.unknown_token", "GET /api/shares/{random}", bogus.status_code == 404, bogus)
    if suspended_token:
        held = await anonymous.get(f"/api/shares/{suspended_token}")
        add("share.suspended_company", "GET /api/shares/{suspended}", held.status_code == 404, held)
    revoked = await owner.call("DELETE", f"/api/conversations/{conversation_id}/share")
    add("share.revoke", "DELETE /api/conversations/{id}/share", revoked.status_code == 204, revoked)
    after = await anonymous.get(f"/api/shares/{token}")
    add("share.revoked_hidden", "GET /api/shares/{revoked}", after.status_code == 404, after)
    return out
