"""I-05 staging security probes (plan 16.6 security checklist, 8.9, 15.2.1).

Run (repo root; inventory from the deployed commit)::

    PYTHONPATH=backend backend/.venv/bin/python scripts/i05_route_inventory.py --out routes.json
    backend/.venv/bin/python scripts/i05_security_check.py --config i05.json \
        --i-know-this-is-staging [--dry-run] [--checks idor,csrf] [--include-mutations]

Exit codes: 0 no failures, 1 a probe failed, 2 refused (guard/config).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import i05_security_probes as probes
import i05_security_transport as transport
from i05_http import login, make_client
from i05_report import Finding, default_output_dir, findings_markdown, now_iso, write_reports
from i05_route_inventory import families, load_inventory, plan_csrf, plan_idor
from i05_safety import GuardError, HarnessConfig, check_target, load_config

CHECKS = ("idor", "csrf", "org_cookie", "rate_limit", "share", "transport", "rls_parity")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "I-05").strip().splitlines()[0])
    parser.add_argument("--config", type=Path, default=os.environ.get("I05_CONFIG"))
    parser.add_argument("--base-url", default=os.environ.get("I05_BASE_URL"))
    parser.add_argument("--checks", default=",".join(CHECKS))
    parser.add_argument("--include-mutations", action="store_true")
    parser.add_argument("--include-delete", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-http", action="store_true")
    parser.add_argument("--i-know-this-is-staging", dest="staging_ack", action="store_true")
    return parser.parse_args(argv)


def build_plan(config: HarnessConfig, args: argparse.Namespace) -> dict[str, Any]:
    sec = config.section("security")
    inventory_path = sec.get("inventory")
    if not inventory_path:
        raise GuardError("security.inventory (route inventory JSON) is required")
    entries = load_inventory(Path(str(inventory_path)))
    idor_probes, unprobed = plan_idor(
        entries,
        dict(sec.get("foreign") or {}),
        dict(sec.get("own") or {}),
        include_mutations=args.include_mutations,
        include_delete=args.include_delete,
    )
    csrf_probes = plan_csrf(entries, sec.get("csrf_limit"))
    covered = {probe.family for probe in idor_probes}
    return {
        "entries": entries,
        "idor": idor_probes,
        "csrf": csrf_probes,
        "summary": {
            "routes": len(entries),
            "idor_probes": len(idor_probes),
            "idor_by_family": dict(Counter(p.family for p in idor_probes)),
            "families_without_probe": sorted(families(entries) - covered),
            "unprobed": unprobed,
            "csrf_probes": len(csrf_probes),
        },
    }


async def run(
    config: HarnessConfig, plan: dict[str, Any], args: Any, checks: set[str]
) -> list[Finding]:
    sec = config.section("security")
    out: list[Finding] = []
    https = config.base_url.startswith("https://")
    prober = await login(
        config.base_url, config.persona(str(sec["prober"])), timeout_s=args.timeout
    )
    others: list[Any] = []
    try:
        if "idor" in checks:
            out += await probes.idor(prober, plan["idor"])
        if "csrf" in checks:
            out += await probes.csrf(prober, plan["csrf"])
        if "org_cookie" in checks and sec.get("foreign_org_id"):
            out += await probes.forged_org_cookie(prober, str(sec["foreign_org_id"]))
        if "rate_limit" in checks and sec.get("other_company"):
            quiet = await login(config.base_url, config.persona(str(sec["other_company"])))
            others.append(quiet)
            out += await probes.rate_limit_isolation(prober, quiet)
        share = sec.get("share") or {}
        if "share" in checks and share.get("conversation_id"):
            async with make_client(config.base_url, args.timeout) as anonymous:
                env = share.get("suspended_share_token_env")
                out += await probes.share_links(
                    prober,
                    anonymous,
                    str(share["conversation_id"]),
                    share.get("foreign_artifact_id"),
                    os.environ.get(str(env)) if env else None,
                )
        if "transport" in checks:
            out += transport.check_cookies(prober, https)
            out += transport.check_headers(await prober.call("GET", "/api/auth/me"), https)
            if https:
                parts = urlsplit(config.base_url)
                out.append(await transport.check_plain_http(config.base_url, None))
                out.append(transport.check_tls(parts.hostname or "", parts.port or 443))
        parity = sec.get("rls_parity") or {}
        if "rls_parity" in checks and parity.get("base_url_rls_off"):
            off_url = check_target(str(parity["base_url_rls_off"]), staging_ack=args.staging_ack)
            off = await login(off_url, config.persona(str(sec["prober"])))
            others.append(off)
            out += await transport.rls_parity(prober, off, list(parity.get("paths") or []))
    finally:
        for session in (prober, *others):
            await session.aclose()
    return out


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.config is None:
            raise GuardError("--config (or I05_CONFIG) is required")
        config = load_config(Path(args.config), args.base_url)
        base = check_target(
            config.base_url, staging_ack=args.staging_ack, allow_http=args.allow_http
        )
        config = HarnessConfig(base, config.personas, config.sections)
        checks = {c.strip() for c in args.checks.split(",") if c.strip()}
        if checks - set(CHECKS):
            raise GuardError(f"unknown checks: {sorted(checks - set(CHECKS))}")
        if not config.section("security").get("prober"):
            raise GuardError("security.prober persona is required")
        plan = build_plan(config, args)
        if args.dry_run:
            summary = {"base_url": base, "checks": sorted(checks), **plan["summary"]}
            summary["missing_env"] = config.missing_env()
            print(json.dumps(summary, indent=2))
            return 0
        if config.missing_env():
            raise GuardError(f"unset secret variables: {config.missing_env()}")
    except (GuardError, ValueError, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    meta = {"base_url": config.base_url, "started_at": now_iso(), "checks": sorted(checks)}
    findings = asyncio.run(run(config, plan, args, checks))
    payload = {
        **meta,
        "plan": plan["summary"],
        "findings": [finding.as_dict() for finding in findings],
    }
    out_dir = args.output_dir or default_output_dir("security")
    report = findings_markdown(meta, findings)
    for path in write_reports(out_dir, "security-report", payload, report):
        print(f"wrote {path}")
    return 1 if any(finding.verdict == "fail" for finding in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
