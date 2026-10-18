"""I-05 security probe logic: route enumeration, 404 parity, CSRF, cookies, share, CLI."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import i05_security_check  # noqa: E402
import i05_security_probes as probes  # noqa: E402
import i05_security_transport as transport  # noqa: E402
from i05_http import Session, login  # noqa: E402
from i05_route_inventory import (  # noqa: E402
    RouteEntry,
    dump_inventory,
    family,
    path_params,
    plan_csrf,
    plan_idor,
)
from i05_safety import Persona  # noqa: E402

OWN = "11111111-1111-4111-8111-111111111111"
FOREIGN = "ffffffff-ffff-4fff-8fff-ffffffffffff"
CSRF = "c1"
COOKIE = "; HttpOnly; Path=/; SameSite=lax; Secure"


def handler(request: httpx.Request) -> httpx.Response:
    path, method = request.url.path, request.method
    if path == "/api/auth/login":
        headers = [("set-cookie", f"moldy_at=a{COOKIE}"), ("set-cookie", f"moldy_rt=r{COOKIE}")]
        body = {"csrf_token": CSRF, "user": {"id": "u1"}}
        return httpx.Response(200, json=body, headers=headers)
    if method != "GET" and request.headers.get("x-csrf-token") != CSRF:
        return httpx.Response(403, json={"error": {"code": "csrf_mismatch"}})
    if path == "/api/me/active-org":
        cookie = "moldy_active_org=o1; Path=/; SameSite=lax; Secure"  # HttpOnly missing
        return httpx.Response(200, json={}, headers={"set-cookie": cookie})
    if path.startswith("/api/skills/"):  # deliberately leaky family
        return httpx.Response(200, json={"id": path.rsplit("/", 1)[-1]})
    if path.endswith(OWN):
        return httpx.Response(200, json={"id": OWN})
    return httpx.Response(404, json={"error": {"code": "not_found"}})


def _session(monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("I05_T_PW", "pw")
    persona = Persona("a", "a@x.test", "I05_T_PW", org_id="o1")
    transport_ = httpx.MockTransport(handler)
    return asyncio.run(login("http://localhost", persona, transport=transport_))


ENTRIES = [
    RouteEntry("GET", "/api/agents/{agent_id}", "RES", "agent_id", "agent"),
    RouteEntry("GET", "/api/skills/{skill_id}", "RES", "skill_id", "skill"),
    RouteEntry("PUT", "/api/agents/{agent_id}", "RES", "agent_id", "agent"),
    RouteEntry("DELETE", "/api/skills/{skill_id}", "RES", "skill_id", "skill"),
    RouteEntry(
        "GET",
        "/api/conversations/{conversation_id}/artifacts/{artifact_id}",
        "PRIVATE",
        "conversation_id",
    ),
    RouteEntry("GET", "/api/tools/{tool_id}", "RES", "tool_id", "tool"),
    RouteEntry("GET", "/api/shares/{share_token}", "SHARE_TOKEN"),
    RouteEntry("GET", "/api/agents", "CANDIDATES", None, "agent"),
]


def test_path_params_and_family() -> None:
    assert path_params("/api/skills/{skill_id}/files/{file_path:path}") == ["skill_id", "file_path"]
    assert family("/api/resources/agent/{resource_id}/grants") == "/api/resources/agent"
    assert family("/api/agents/{agent_id}") == "/api/agents"


def test_plan_idor_covers_nested_and_reports_unprobed() -> None:
    foreign = {"agent_id": FOREIGN, "skill_id": FOREIGN, "artifact_id": FOREIGN}
    found, unprobed = plan_idor(ENTRIES, foreign, {"conversation_id": OWN})
    templates = {p.method + " " + p.template for p in found}
    assert "GET /api/conversations/{conversation_id}/artifacts/{artifact_id}" in templates
    nested = next(p for p in found if "artifacts" in p.template)
    assert OWN in nested.path and FOREIGN in nested.path and FOREIGN not in nested.control_path
    reasons = {row["route"]: row["reason"] for row in unprobed}
    assert "mutation skipped" in reasons["PUT /api/agents/{agent_id}"]
    assert "DELETE skipped" in reasons["DELETE /api/skills/{skill_id}"]
    assert "no foreign fixture" in reasons["GET /api/tools/{tool_id}"]
    assert all("shares" not in p.template for p in found)  # token routes are not IDOR targets
    with_mutations, _ = plan_idor(ENTRIES, foreign, include_mutations=True, include_delete=True)
    assert {"PUT", "DELETE"} <= {p.method for p in with_mutations}


def test_plan_csrf_targets_only_session_mutations_with_nonexistent_ids() -> None:
    found = plan_csrf(ENTRIES)
    assert {(p.method, p.template) for p in found} == {
        ("PUT", "/api/agents/{agent_id}"),
        ("DELETE", "/api/skills/{skill_id}"),
    }
    assert all(FOREIGN not in p.path and "{" not in p.path for p in found)


def test_real_app_inventory_enumerates_every_resource_family() -> None:
    entries = dump_inventory()
    kinds = {e.kind for e in entries}
    assert "NONE" not in kinds and "E2E_ONLY" not in kinds
    res = [e for e in entries if e.kind in ("RES", "PRIVATE") and path_params(e.path)]
    assert len(res) > 150
    for entry in res:  # the policy parameter is a real path parameter or a known query one
        assert entry.param is None or entry.param in path_params(entry.path) or entry.kind == "RES"
    fixtures = {name: FOREIGN for e in entries for name in path_params(e.path)}
    found, unprobed = plan_idor(entries, fixtures, include_mutations=True, include_delete=True)
    assert {"/api/agents", "/api/conversations", "/api/skills", "/api/credentials"} <= {
        p.family for p in found
    }
    assert unprobed == []


def test_idor_probe_flags_leaky_family_and_passes_hidden_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(monkeypatch)
    found, _ = plan_idor(ENTRIES[:2], {"agent_id": FOREIGN, "skill_id": FOREIGN})
    results = {f.target: f for f in asyncio.run(probes.idor(session, found))}
    assert results["GET /api/agents/{agent_id}"].verdict == "pass"
    leaky = results["GET /api/skills/{skill_id}"]
    assert leaky.verdict == "fail" and leaky.detail.startswith("CRITICAL")


def test_csrf_probe_passes_when_mutations_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(monkeypatch)
    findings = asyncio.run(probes.csrf(session, plan_csrf(ENTRIES)))
    assert len(findings) == 4 and all(f.verdict == "pass" for f in findings)


def test_cookie_check_flags_active_org_attribute_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(monkeypatch)
    results = {(f.check, f.target): f for f in transport.check_cookies(session, https=True)}
    assert results[("cookie.auth_flags", "moldy_at")].verdict == "pass"
    drift = results[("cookie.active_org_attrs", "moldy_active_org")]
    assert drift.verdict == "fail" and "httponly" in drift.detail


def test_ids_of_reads_bare_lists_and_pages() -> None:
    assert transport.ids_of([{"id": 1}, {"id": "2"}]) == {"1", "2"}
    assert transport.ids_of({"items": [{"id": "a"}], "next_cursor": None}) == {"a"}
    assert transport.ids_of({"unexpected": True}) == set()


def _security_config(tmp_path: Path, base_url: str) -> Path:
    inventory = tmp_path / "routes.json"
    inventory.write_text(json.dumps([asdict(e) for e in ENTRIES]))
    config = {
        "base_url": base_url,
        "personas": {"a": {"email": "a@x.test", "password_env": "I05_UNSET_PW"}},
        "security": {"prober": "a", "inventory": str(inventory), "foreign": {"agent_id": FOREIGN}},
    }
    path = tmp_path / "i05.json"
    path.write_text(json.dumps(config))
    return path


def test_security_cli_guard_and_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    remote = _security_config(tmp_path, "https://staging.example.com")
    assert i05_security_check.main(["--config", str(remote), "--dry-run"]) == 2
    capsys.readouterr()
    argv = ["--config", str(remote), "--dry-run", "--i-know-this-is-staging"]
    assert i05_security_check.main(argv) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["idor_probes"] == 1 and summary["missing_env"] == ["I05_UNSET_PW"]
    assert "/api/skills" in summary["families_without_probe"]
    assert i05_security_check.main(["--config", str(remote), "--i-know-this-is-staging"]) == 2
