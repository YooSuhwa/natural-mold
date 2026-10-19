"""I-05 staging load test (plan 16.6 load item, 8.10 targets).

Run (from the repo root, staging stack in ``AUTHZ_MODE=enforce``)::

    export I05_A_MEMBER_PASSWORD=...   # every *_env name the config lists
    backend/.venv/bin/python scripts/i05_load_test.py --config i05.json \
        --i-know-this-is-staging [--dry-run] [--scenarios fga.check,api.list.agents]

Exit codes: 0 every bound met, 1 a target missed, 2 refused (guard/config).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
from i05_http import login_all, make_client
from i05_load_runner import (
    LoadSettings,
    apply_overrides,
    missing_needs,
    run_scenario,
    select,
    skip_all,
)
from i05_load_scenarios import LoadContext, Scenario, catalog
from i05_report import default_output_dir, load_markdown, now_iso, write_reports
from i05_safety import GuardError, HarnessConfig, check_target, load_config
from i05_stats import MetricResult, overall_failed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "I-05").strip().splitlines()[0])
    parser.add_argument("--config", type=Path, default=os.environ.get("I05_CONFIG"))
    parser.add_argument("--base-url", default=os.environ.get("I05_BASE_URL"))
    parser.add_argument("--scenarios", default="", help="comma list; default all non-opt-in")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--ramp-seconds", type=float, default=0.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--label", default="", help='report label, e.g. "local rehearsal"')
    parser.add_argument("--dry-run", action="store_true", help="validate and print the plan")
    parser.add_argument("--allow-http", action="store_true")
    parser.add_argument("--i-know-this-is-staging", dest="staging_ack", action="store_true")
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    return parser.parse_args(argv)


def plan(
    config: HarnessConfig, chosen: Sequence[Scenario], settings: LoadSettings
) -> dict[str, Any]:
    overrides = config.section("targets")
    return {
        "base_url": config.base_url,
        "settings": asdict(settings),
        "missing_env": config.missing_env(),
        "scenarios": [
            {
                "name": s.name,
                "opt_in": s.opt_in,
                "concurrency": s.concurrency or settings.concurrency,
                "missing_config": missing_needs(s, config.sections),
                "targets": {
                    k: {"p95_ms": t.p95_ms, "source": t.source}
                    for k, t in apply_overrides(s, overrides).items()
                },
            }
            for s in chosen
        ],
    }


def _fga_client(
    config: HarnessConfig, timeout: float, ack: bool, allow_http: bool
) -> httpx.AsyncClient | None:
    fga = config.section("fga")
    if not fga.get("url"):
        return None
    url = check_target(str(fga["url"]), staging_ack=ack, allow_http=allow_http)
    headers: dict[str, str] = {}
    if fga.get("token_env"):
        headers["Authorization"] = f"Bearer {os.environ[str(fga['token_env'])]}"
    return make_client(url, timeout, headers=headers)


async def execute(
    config: HarnessConfig, chosen: Sequence[Scenario], settings: LoadSettings, args: Any
) -> list[MetricResult]:
    sections = config.sections
    users = {
        str(sections[key][role])
        for key, role in (("load", "user"), ("run_start", "user"), ("share", "owner"))
        if isinstance(sections.get(key), dict) and sections[key].get(role)
    }
    if isinstance(sections.get("share"), dict) and sections["share"].get("recipient"):
        users.add(str(sections["share"]["recipient"]))
    personas = [config.persona(name) for name in sorted(users)]
    sessions = await login_all(config.base_url, personas, timeout_s=args.timeout)
    fga = _fga_client(config, args.timeout, args.staging_ack, args.allow_http)
    ctx = LoadContext(sessions=sessions, sections=sections, fga=fga)
    results: list[MetricResult] = []
    try:
        for scenario in chosen:
            targets = apply_overrides(scenario, config.section("targets"))
            missing = missing_needs(scenario, sections)
            if missing:
                results += skip_all(scenario, f"config missing {missing}", targets)
                continue
            print(f"running {scenario.name} ...", flush=True)
            results += await run_scenario(scenario, ctx, settings, targets)
    finally:
        for session in sessions.values():
            await session.aclose()
        if fga is not None:
            await fga.aclose()
    return results


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list:
        for scenario in catalog():
            print(f"{scenario.name}{' (opt-in)' if scenario.opt_in else ''}")
        return 0
    try:
        if args.config is None:
            raise GuardError("--config (or I05_CONFIG) is required")
        config = load_config(Path(args.config), args.base_url)
        base = check_target(
            config.base_url, staging_ack=args.staging_ack, allow_http=args.allow_http
        )
        config = HarnessConfig(base, config.personas, config.sections)
        if config.section("fga").get("url"):
            ack, allow = args.staging_ack, args.allow_http
            check_target(str(config.section("fga")["url"]), staging_ack=ack, allow_http=allow)
        settings = LoadSettings(args.concurrency, args.iterations, args.warmup, args.ramp_seconds)
        requested = [name.strip() for name in args.scenarios.split(",") if name.strip()]
        chosen, unknown = select(catalog(), requested)
        if unknown:
            raise GuardError(f"unknown scenarios: {unknown}")
        if args.dry_run:
            print(json.dumps(plan(config, chosen, settings), indent=2, default=str))
            return 0
        if config.missing_env():
            raise GuardError(f"unset secret variables: {config.missing_env()}")
    except (GuardError, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    meta = {"base_url": config.base_url, "label": args.label, "started_at": now_iso()}
    meta["settings"] = asdict(settings)
    results = asyncio.run(execute(config, chosen, settings, args))
    payload = {**meta, "results": [r.as_dict() for r in results]}
    out_dir = args.output_dir or default_output_dir("load")
    for path in write_reports(out_dir, "load-report", payload, load_markdown(meta, results)):
        print(f"wrote {path}")
    return 1 if overall_failed(results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
