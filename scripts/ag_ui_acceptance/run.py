"""Verify ordinary-chat cutover on two workers in a disposable multi-tenant stack."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import sys
import time
import traceback
from collections.abc import Callable
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import NAMESPACE_URL, uuid5

import httpx

# Direct CLI invocation puts this directory on sys.path. Add the shared E2E lane helpers.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from case_selection import resolve_case_specs
from e2e_runner_contract import E2eDsns
from e2e_runner_environment import assert_node22, build_lane_environment
from evidence import collect_failure, playwright_report
from identities import provision_identities
from native_postgres import native_postgres
from openfga import acceptance_openfga
from processes import command, managed, ports, wait_ready
from skill_attempts import docx_attempt_environment
from stack_sources import digest, revision, stage, stage_probe_files

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parents[1]


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=HARNESS,
        help="local repository that contains the product revision",
    )
    parser.add_argument(
        "--revision",
        default="HEAD",
        help="immutable product revision (resolved before any processes start)",
    )
    parser.add_argument(
        "--deps",
        type=Path,
        default=HARNESS,
        help="checkout with installed locked workspace packages",
    )
    parser.add_argument("--skill-deps", type=Path, help="optional locked skill dependency checkout")
    parser.add_argument(
        "--reference-python",
        type=Path,
        help="Python from a separate environment with the official LangGraph adapter",
    )
    parser.add_argument(
        "--perf-timing",
        action="store_true",
        help="measure model emission, PostgreSQL commit, client receipt and DOM display per token",
    )
    parser.add_argument(
        "--verify-default",
        action="store_true",
        help="verify that a frontend built and started with no transport override uses AG-UI",
    )
    parser.add_argument("--phase", choices=("smoke", "api", "browser", "full"), default="full")
    parser.add_argument("--output", type=Path, required=True, help="new local evidence directory")
    parser.add_argument(
        "--extra-probe",
        type=Path,
        action="append",
        default=[],
        help="optional additional local isolated acceptance script",
    )
    parser.add_argument(
        "--delegation-probe",
        type=Path,
        help="local child-control probe run on dedicated loopback-model workers",
    )
    parser.add_argument(
        "--extra-spec",
        action="append",
        default=[],
        help="additional e2e relative spec after main cutover",
    )
    parser.add_argument(
        "--ag-ui-spec",
        action="append",
        default=[],
        help="additional e2e spec that is meaningful only on the standard client",
    )
    parser.add_argument(
        "--extra-case",
        action="append",
        default=[],
        help="select a single normal case in both modes by e2e/file.spec.ts::exact test title",
    )
    args = parser.parse_args()
    if args.perf_timing and args.phase not in {"browser", "full"}:
        parser.error("--perf-timing requires a browser or full gate")
    if args.verify_default and args.phase not in {"browser", "full"}:
        parser.error("--verify-default requires a browser or full gate")
    for spec in (*args.extra_spec, *args.ag_ui_spec):
        if not re.fullmatch(
            r"e2e/(?:[a-zA-Z0-9_-]+/)*[a-zA-Z0-9_-]+\.spec\.ts(?::[1-9][0-9]*)?", spec
        ):
            parser.error(
                "--extra-spec must identify a single relative e2e spec, optionally with :line"
            )
    return args


def browser_gate(
    modes: dict[str, str],
    env: dict[str, str],
    root: Path,
    output: Path,
    logs: Path,
    stack: ExitStack,
    extra_specs: list[str],
    extra_cases: list[str],
    ag_ui_specs: list[str],
    verify_default: bool,
    probes: Path,
    record: dict,
    start_delegation_pair: Callable[[], None],
) -> None:
    frontend = root / "frontend"
    frontend_envs: dict[str, dict[str, str]] = {}
    record["frontend_transport_environment"] = {}
    command("assets", ["pnpm", "run", "prepare:assets"], frontend, env, logs)
    for mode, url in modes.items():
        port = str(httpx.URL(url).port)
        dist = frontend / ".next" / mode
        dist.mkdir(parents=True, exist_ok=True)
        build_env = {
            **env,
            "E2E_NEXT_BUILD_DIR": str(dist),
            "NEXT_PUBLIC_CHAT_RUNTIME": mode,
            "NEXT_PUBLIC_API_BASE_URL": env["E2E_API_BASE_URL"],
            "E2E_BASE_URL": url,
            "E2E_FRONTEND_PORT": port,
            "NEXT_TELEMETRY_DISABLED": "1",
        }
        if verify_default and mode == "ag_ui":
            del build_env["NEXT_PUBLIC_CHAT_RUNTIME"]
        record["frontend_transport_environment"][mode] = {
            "build_and_start_override": build_env.get("NEXT_PUBLIC_CHAT_RUNTIME"),
            "expected_browser_transport": mode,
        }
        command(
            f"build-{mode}",
            ["pnpm", "exec", "next", "build", "--webpack"],
            frontend,
            build_env,
            logs,
            900,
        )
        proc, log = stack.enter_context(
            managed(
                f"next-{mode}",
                [
                    "pnpm",
                    "exec",
                    "next",
                    "start",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    port,
                ],
                frontend,
                build_env,
                logs,
            )
        )
        wait_ready(proc, log, build_env, url=f"{url}/login", timeout=90)
        cache = dist / "cache" / "webpack"
        if cache.is_dir() and not cache.is_symlink() and cache.resolve().is_relative_to(root):
            # Next only reads its webpack cache on a subsequent compilation.
            shutil.rmtree(cache)
        frontend_envs[mode] = {**build_env, "NEXT_PUBLIC_CHAT_RUNTIME": mode}
        record["processes"][f"next-{mode}"] = proc.pid
    # Companion probes may now reuse the same authenticated, production-built frontends.
    env["MOLDY_AGUI_BROWSER_READY"] = "1"
    extra_specs = resolve_case_specs(
        frontend, frontend_envs["ag_ui"], logs, probes, extra_cases, extra_specs
    )
    record["selected_browser_cases"] = {"case_titles": extra_cases, "resolved_specs": extra_specs}
    suites = [
        ("performance-langgraph_v3", "langgraph_v3", ["e2e/chat-stream-perf.spec.ts"]),
        ("performance-ag_ui", "ag_ui", ["e2e/chat-stream-perf.spec.ts"]),
        ("cutover", "ag_ui", ["e2e/manual/chat-ag-ui-cutover.spec.ts"]),
    ]
    independent = "e2e/independent-chat-jobs.spec.ts"
    run_delegation = independent in extra_specs
    ordinary = [spec for spec in extra_specs if spec != independent]
    if ag_ui_specs:
        suites.append(("standard-client-regressions", "ag_ui", ag_ui_specs))
    if ordinary:
        suites.extend(
            (f"ordinary-regressions-{mode}", mode, ordinary) for mode in ("langgraph_v3", "ag_ui")
        )
    if run_delegation:
        suites.extend(
            (f"independent-{mode}", mode, [independent]) for mode in ("langgraph_v3", "ag_ui")
        )
    failed = []
    for name, mode, specs in suites:
        if name == "independent-langgraph_v3":
            start_delegation_pair()
        report = {"passed": False, "errors": ["missing_playwright_report"]}
        command_failed = False
        cfg = {
            **frontend_envs[mode],
            "MOLDY_AGUI_PLAYWRIGHT_GROUP": name,
            "MOLDY_AGUI_PLAYWRIGHT_JSON": str(logs / f"pw-{name}.json"),
        }
        try:
            command(
                name,
                [
                    "pnpm",
                    "exec",
                    "playwright",
                    "test",
                    "-c",
                    str(probes / "playwright.cutover.cjs"),
                    *specs,
                ],
                frontend,
                cfg,
                logs,
                1800,
            )
        except RuntimeError:
            command_failed = True
        finally:
            raw = Path(cfg["MOLDY_AGUI_PLAYWRIGHT_JSON"])
            if raw.is_file():
                report = playwright_report(
                    json.loads(raw.read_text()),
                    root,
                    group=name,
                    specs=specs,
                    performance_timing=env.get("E2E_AGUI_PERF_TIMING") == "1",
                )
            record["browser"][name] = report
            (output / f"{name}-results.json").write_text(json.dumps(report, indent=2) + "\n")
        if command_failed or not report["passed"]:
            failed.append(name)
    if failed:
        raise RuntimeError(f"Browser suites requiring attention: {', '.join(failed)}")


def execute(args: argparse.Namespace, record: dict) -> None:
    assert_node22()
    source, deps, output = (
        args.source.resolve(),
        args.deps.resolve(),
        args.output.resolve(),
    )
    sha = revision(source, args.revision)
    record.update(source_commit=sha, phase=args.phase, processes={}, browser={})
    resources: list[Path] = []

    def check_cleanup() -> None:
        remaining = [path.name for path in resources if path.exists()]
        processes = []
        for name, pid in record["processes"].items():
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            processes.append(name)
        record["cleaned"] = not remaining and not processes
        if remaining or processes:
            raise RuntimeError(f"Owned resources survived cleanup: {remaining}, {processes}")

    with ExitStack() as stack:
        stack.callback(check_cleanup)
        root = Path(
            stack.enter_context(TemporaryDirectory(prefix=".moldy-test-run.agui-"))
        ).resolve()
        resources.append(root)
        logs = root / "logs"
        logs.mkdir(mode=0o700)
        stack.push(lambda t, e, tb: collect_failure(logs, record, t, e, tb))
        probes = stage_probe_files(HERE, root)
        record["acceptance_commit"] = revision(HARNESS, "HEAD")
        record["fixtures"] = json.loads((root / "probe-files.json").read_text())

        def selected_probe(file: Path) -> Path:
            path = file.resolve(strict=True)
            if path.is_relative_to(HERE):
                return probes / path.relative_to(HERE)
            record.setdefault("external_probes_sha256", {})[path.name] = digest(path)
            return path

        use_browser = args.phase in {"browser", "full"}
        repo = stage(
            source,
            sha,
            deps,
            root,
            probes.parents[1],
            use_browser,
            skill_deps=args.skill_deps.resolve() if args.skill_deps else None,
            require_protocol=args.phase != "smoke",
        )
        record["dependencies"] = json.loads((root / "source-dependencies.json").read_text())
        backend = repo / "backend"
        fga = stack.enter_context(acceptance_openfga(backend))
        pg = stack.enter_context(native_postgres())
        resources.append(pg.data_dir)
        dsns = E2eDsns(pg.database_url, pg.database_url_sync, pg.database_url)
        env = build_lane_environment("scripted", "scripted-full", dsns, root)
        node = shutil.which("node", path=env.get("PATH"))
        if node is None:
            raise RuntimeError("Node 22 is required for actual skill processes")
        env.update(docx_attempt_environment(root, node))
        a, b, proxy, web, previous = ports(5)
        modes = {
            "ag_ui": f"http://localhost:{web}",
            "langgraph_v3": f"http://localhost:{previous}",
        }
        urls = {label: f"http://localhost:{port}" for label, port in (("a", a), ("b", b))}
        scope = uuid5(NAMESPACE_URL, "moldy/e2e-organization")
        env.update(
            {
                **fga,
                "MOLDY_BACKEND_SOURCE_ROOT": str(backend),
                "DEPLOYMENT_MODE": "multi_tenant",
                "REGISTRATION_POLICY": "personal_tenant",
                "ALLOW_FIRST_USER_AS_ADMIN": "false",
                "DB_RLS_ENABLED": "true",
                "DATABASE_URL": pg.database_url,
                "DATABASE_URL_SYNC": pg.database_url_sync,
                "MIGRATION_DATABASE_URL": pg.migration_database_url,
                "CHAT_STEER_ENABLED": "true",
                "NEXT_PUBLIC_CHAT_STEER_ENABLED": "true",
                "CHAT_WORK_NOTIFICATIONS_ENABLED": "true",
                "E2E_CHAT_GOAL_NOTIFICATIONS": "1",
                "CONVERSATION_GOAL_ORG_IDS": json.dumps([str(scope)]),
                "E2E_FRONTEND_PORT": str(web),
                "E2E_BACKEND_PORT": str(proxy),
                "E2E_API_BASE_URL": f"http://localhost:{proxy}",
                "CORS_ALLOWED_ORIGINS": ",".join(modes.values()),
                "SKILL_EVALUATION_ENABLED": "true",
                "MOLDY_AGUI_PRIMARY_URL": urls["a"],
                "MOLDY_AGUI_SECONDARY_URL": urls["b"],
                "MOLDY_AGUI_EVIDENCE_DIR": str(output),
                "MOLDY_AGUI_CUTOVER": "1",
                "E2E_AGUI_LEGACY_FRONTEND_URL": modes["langgraph_v3"],
                "MOLDY_AGUI_OBSERVER": str(probes / "observe.py"),
                "MOLDY_AGUI_OBSERVER_URL": pg.admin_database_url,
                "MOLDY_AGUI_PROXY_RECEIPT": str(output / "replicas.json"),
                "SKILL_NODE_MODULES_DIR": str(backend / "skill-node/node_modules"),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        if args.perf_timing:
            model_timings = output / "fixture-emission.jsonl"
            model_timings.touch(mode=0o600, exist_ok=False)
            env.update(
                {
                    "E2E_AGUI_PERF_TIMING": "1",
                    "MOLDY_AGUI_MODEL_TIMINGS": str(model_timings),
                    "MOLDY_AGUI_PERF_MODEL_WRAPPER": str(probes / "perf_model.py"),
                    "MOLDY_AGUI_PERF_OBSERVER": str(probes / "perf_observe.py"),
                }
            )
        record["locks"] = {
            name: digest(repo / name) for name in ("pnpm-lock.yaml", "backend/uv.lock")
        }
        command(
            "migration",
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            backend,
            env,
            logs,
        )
        pg.assert_application_access()
        runtime_env = {
            k: v
            for k, v in env.items()
            if k
            not in {
                "MIGRATION_DATABASE_URL",
                "MOLDY_AGUI_OBSERVER_URL",
                "MOLDY_AGUI_OBSERVER",
            }
        }
        # Migration mode determines role separation at app startup. Only migrations use the
        # admin DSN. The two independent worker identities each get just the non-owner app DSN.
        from stack_workers import worker_pair

        active_workers = stack.enter_context(ExitStack())
        active_workers.enter_context(
            worker_pair(backend, logs, runtime_env, {"a": a, "b": b}, record)
        )

        delegation_active = False

        def start_delegation_pair() -> None:
            nonlocal delegation_active
            if delegation_active:
                return
            active_workers.close()
            delegation_env = {
                **runtime_env,
                "INDEPENDENT_CHAT_JOBS_ENABLED": "true",
                "CHILD_TASKS_ENABLED": "true",
                "CHAT_WORK_NOTIFICATIONS_ENABLED": "true",
                "CREDENTIAL_HTTP_ALLOWED_HOSTS": "127.0.0.1",
                "OUTBOUND_PRIVATE_ALLOWED_HOSTS": "127.0.0.1",
            }
            active_workers.enter_context(
                worker_pair(
                    backend,
                    logs,
                    delegation_env,
                    {"a": a, "b": b},
                    record,
                    prefix="delegation",
                )
            )
            delegation_active = True

        proc, log = stack.enter_context(
            managed(
                "proxy",
                [sys.executable, str(probes / "replica_proxy.py"), str(proxy)],
                probes,
                runtime_env,
                logs,
            )
        )
        wait_ready(proc, log, runtime_env, url=env["E2E_API_BASE_URL"] + "/api/health")
        record["processes"]["proxy"] = proc.pid
        with httpx.Client(trust_env=False, timeout=10) as client:
            for label in ("a", "b"):
                r = client.get(
                    env["E2E_API_BASE_URL"] + "/api/health",
                    headers={"x-moldy-e2e-worker": label},
                )
                r.raise_for_status()
                if r.headers.get("x-moldy-e2e-worker") != label:
                    raise RuntimeError("Replica routing mismatch")
        identities = provision_identities(
            urls["a"], urls["b"], env["E2E_USER_EMAIL"], env["E2E_USER_PASSWORD"]
        )
        env.update(identities.environment)
        record.update(identities=identities.evidence, postgres_role_verified=True)
        final_errors = []
        if args.phase in {"api", "full"}:
            try:
                command(
                    "http-contract",
                    [
                        sys.executable,
                        str(probes / "http_contract.py"),
                        "--case",
                        "approval",
                        "--case",
                        "compatibility",
                        "--case",
                        "start",
                        "--case",
                        "detach",
                        "--case",
                        "termination",
                    ],
                    backend,
                    env,
                    logs,
                    900,
                )
                record["http_verification"] = "passed"
            except RuntimeError as exc:
                record["http_verification"] = "failed"
                final_errors.append(str(exc))
            from sdk_verification import typescript_receipts

            sdk = typescript_receipts(root, output, env, logs)
            record["typescript_sdk"] = {
                "status": sdk["status"],
                "file": "sdk-transcripts.json",
            }
            if sdk["status"] != "passed":
                final_errors.append("Incomplete or invalid public SDK request or event sequence")
            if args.reference_python:
                try:
                    record["reference_comparison"] = {"status": "running"}
                    label = "reference-comparison"
                    command(
                        label,
                        [
                            str(args.reference_python.absolute()),
                            str(probes / "compare_approval.py"),
                            str(output / "ag-ui-approval-single-interrupt.json"),
                            str(output / "ag-ui-approval-single-continuation.json"),
                        ],
                        root,
                        env,
                        logs,
                    )
                    # The reference prints one JSON line. Parse only its schema-shaped result,
                    # excluding diagnostics emitted separately by the upstream SDK.
                    reports = []
                    for line in (logs / f"{label}.log").read_text().splitlines():
                        if line.startswith('{"versions":'):
                            reports.append(json.loads(line))
                    if len(reports) != 1 or reports[0].keys() != {
                        "versions",
                        "reference",
                        "observed",
                    }:
                        raise RuntimeError(
                            "Missing official structured approval comparison receipt"
                        )
                    (output / f"{label}.json").write_text(json.dumps(reports[0], indent=2) + "\n")
                    record["reference_comparison"] = {
                        "status": "passed",
                        "file": f"{label}.json",
                    }
                except (ValueError, OSError, RuntimeError) as exc:
                    record["reference_comparison"] = {"status": "failed"}
                    final_errors.append(str(exc))
            else:
                record["reference_comparison"] = {"status": "not_requested"}
        for probe in args.extra_probe:
            command(
                probe.stem,
                [sys.executable, str(selected_probe(probe))],
                backend,
                env,
                logs,
                300,
            )
        try:
            if use_browser:
                browser_gate(
                    modes,
                    env,
                    root,
                    output,
                    logs,
                    stack,
                    args.extra_spec,
                    args.extra_case,
                    args.ag_ui_spec,
                    args.verify_default,
                    probes,
                    record,
                    start_delegation_pair,
                )
        except RuntimeError as exc:
            final_errors.append(str(exc))
        if args.delegation_probe:
            try:
                start_delegation_pair()
                command(
                    "delegation-probe",
                    [sys.executable, str(selected_probe(args.delegation_probe))],
                    backend,
                    env,
                    logs,
                    600,
                )
                record["delegation_probe"] = "passed"
            except RuntimeError as exc:
                record["delegation_probe"] = "failed"
                final_errors.append(str(exc))
        if final_errors:
            raise RuntimeError("\n".join(final_errors))


def main() -> int:
    args = arguments()
    args.output = args.output.resolve()
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)

    def stop(signum: int, _frame: object) -> None:
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, stop)
    started = time.monotonic()
    record = {"started_at": datetime.now(UTC).isoformat(), "passed": False}
    try:
        execute(args, record)
        record["passed"] = True
    except (KeyboardInterrupt, Exception) as exc:  # noqa: BLE001 - always close owned resources and write the receipt
        record["failure"] = type(exc).__name__
        record["failure_location"] = [
            {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(exc.__traceback__)[-8:]
        ]
        # Probe and subprocess errors can contain untrusted output. Only sanitized library
        # exceptions from the process wrapper include log tails. Details stay local in stderr.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        record["elapsed_seconds"] = round(time.monotonic() - started, 2)
        (args.output / "stack.json").write_text(json.dumps(record, indent=2, default=str) + "\n")
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
