from __future__ import annotations

import os

from e2e_runner_cleanup import (
    cleanup_resources,
    publish_runner_receipts,
)
from e2e_runner_cli import run_cli
from e2e_runner_contract import (
    E2eContractError,
    E2eDsns,
    Lane,
    Project,
    SelfTest,
    parse_e2e_dsns,
)
from e2e_runner_environment import assert_node22, build_lane_environment
from e2e_runner_export import ExportReceipt, export_artifacts
from e2e_runner_manifest import build_manifest
from e2e_runner_playwright import (
    PlaywrightReceiptError,
    assert_exact_selection,
    parse_playwright_json,
)
from e2e_runner_process import (
    OwnedProcessInterrupted,
    assert_ports_available,
    run_owned_process,
)
from e2e_runner_proxy import (
    ProxyStartError,
    read_proxy_receipt,
    start_live_proxy,
    stop_live_proxy,
)
from e2e_runner_runtime import (
    E2eResources,
    ProvisioningError,
    provision_resources,
)
from e2e_runner_scenario import (
    cleanup_passed,
    empty_cleanup,
    ensure_results_directory,
    initial_ownership,
    live_egress_passed,
    playwright_argv,
    prepare_live_contract,
    resource_facts,
    secrets_from_environment,
    selection_environment,
)
from postgres_manifest_io import RunnerInterrupted, defer_cleanup_signals


def _run(
    lane: Lane,
    project: Project,
    arguments: tuple[str, ...],
    self_test: SelfTest = "normal",
) -> tuple[dict[str, object], int]:
    resources: E2eResources | None = None
    proxy = None
    selected_ids: tuple[str, ...] = ()
    executed_ids: tuple[str, ...] = ()
    export = ExportReceipt(False, None, (), ())
    cleanup: dict[str, bool | None] = empty_cleanup()
    status = "failed"
    reason: str | None = "not_started"
    child_exit = 70
    process_stopped = True
    runner_receipts_published = True
    secrets_to_scan: tuple[str, ...] = ()
    egress: dict[str, object] = {"enabled": False, "clean_stop": True, "records": []}
    ownership = initial_ownership()
    run_id = os.urandom(12).hex()
    inherited = dict(os.environ)
    try:
        assert_node22()
        assert_ports_available((3100, 8101) if lane == "scripted" else (3200, 8201))
        if lane == "live" and any(
            not inherited.get(name, "").strip()
            for name in ("E2E_LLM_BASE_URL", "E2E_LLM_API_KEY", "E2E_LLM_MODEL")
        ):
            raise RuntimeError("live_llm_configuration_missing")
        resources = provision_resources(lane)
        ownership["run_root"] = True
        ownership["database"] = True
        run_id = resources.run_id
        env = build_lane_environment(lane, project, resources.dsns, resources.run_root)
        if self_test == "dsn-failure":
            parse_e2e_dsns(
                E2eDsns(
                    resources.dsns.async_url,
                    resources.dsns.sync_url.replace(resources.run_id, "mismatch"),
                    resources.dsns.integration_url,
                ),
                lane,
            )
        if self_test == "server-failure":
            env["MOLDY_BACKEND_SOURCE_ROOT"] = str(resources.run_root / "missing-backend")
        secrets_to_scan = secrets_from_environment(
            env,
            postgres_password=resources.postgres_password,
        )
        runner_dir = resources.run_root / "runner"
        runner_dir.mkdir(mode=0o700)
        expected = prepare_live_contract(lane, project, env)
        list_env = selection_environment(lane, env)
        effective_arguments = (
            ("e2e/__forced_missing__.spec.ts",) if self_test == "spec-failure" else arguments
        )
        list_result = run_owned_process(
            playwright_argv(project, effective_arguments, selection_only=True),
            cwd=resources.run_root / "frontend",
            env=list_env,
            stdout_path=runner_dir / "selection.json",
            stderr_path=runner_dir / "selection.log",
        )
        process_stopped = list_result.process_group_stopped
        if list_result.returncode != 0:
            raise RuntimeError("playwright_list_failed")
        selected = parse_playwright_json(runner_dir / "selection.json")
        if any(node.project != project for node in selected):
            raise PlaywrightReceiptError("project_selection_mismatch")
        if expected is not None:
            assert_exact_selection(selected, expected)
        selected_ids = tuple(node.node_id for node in selected)
        ensure_results_directory(resources.run_root, project)
        if self_test == "sigint":
            raise RunnerInterrupted(2)
        if lane == "live":
            proxy = start_live_proxy(resources, inherited)
            ownership["proxy"] = True
            env.update(
                {
                    "E2E_LLM_BASE_URL": proxy.base_url,
                    "E2E_LLM_API_KEY": proxy.token,
                    "E2E_LLM_MODEL": inherited["E2E_LLM_MODEL"],
                }
            )
            secrets_to_scan += (inherited["E2E_LLM_API_KEY"], proxy.token)
        execution = run_owned_process(
            playwright_argv(project, effective_arguments, selection_only=False),
            cwd=resources.run_root / "frontend",
            env={**env, "DEBUG": "pw:webserver"},
            stdout_path=runner_dir / "execution.log",
            stderr_path=runner_dir / "execution.stderr.log",
        )
        if self_test != "server-failure":
            ownership.update({"backend": True, "frontend": True})
        process_stopped = process_stopped and execution.process_group_stopped
        child_exit = execution.returncode
        if execution.returncode != 0:
            reason = "server_start_failed" if self_test == "server-failure" else "playwright_failed"
        execution_receipt = (
            resources.run_root / "frontend/test-results" / project / "execution.json"
        )
        try:
            executed = parse_playwright_json(execution_receipt)
            executed_ids = tuple(node.node_id for node in executed)
        except PlaywrightReceiptError:
            if execution.returncode == 0:
                raise
        if execution.returncode == 0 and executed_ids != selected_ids:
            reason = "execution_selection_mismatch"
        elif execution.returncode == 0:
            status, reason, child_exit = "passed", None, 0
    except OwnedProcessInterrupted as interrupted:
        process_stopped = process_stopped and interrupted.process_group_stopped
        if interrupted.signal_number is None:
            status, reason, child_exit = "failed", "child_interrupted", 70
        else:
            status = "interrupted"
            reason = "signal"
            child_exit = 128 + interrupted.signal_number
    except RunnerInterrupted as interrupted:
        status, reason, child_exit = (
            "interrupted",
            "signal",
            128 + interrupted.signal_number,
        )
    except ProxyStartError as error:
        process_stopped = process_stopped and error.process_group_stopped
        if error.signal_number is None:
            reason = error.reason
        else:
            status, reason, child_exit = (
                "interrupted",
                "signal",
                128 + error.signal_number,
            )
    except ProvisioningError as error:
        cleanup = error.cleanup
        ownership = error.ownership
        if error.signal_number is None:
            reason = error.reason
        else:
            status, reason, child_exit = (
                "interrupted",
                "signal",
                128 + error.signal_number,
            )
    except (E2eContractError, PlaywrightReceiptError, RuntimeError) as error:
        reason = str(error)
    except OSError:
        reason = "child_start_failed"
    finally:
        with defer_cleanup_signals() as deferred:
            try:
                proxy_stopped = stop_live_proxy(proxy)
            except BaseException:  # noqa: BLE001 - finalizer must continue
                proxy_stopped = False
            egress = read_proxy_receipt(proxy, proxy_stopped)
            process_stopped = process_stopped and proxy_stopped
            if resources is not None:
                runner_receipts_published = publish_runner_receipts(resources, project)
                try:
                    export = export_artifacts(resources, lane, project, secrets_to_scan)
                except BaseException:  # noqa: BLE001 - finalizer must continue
                    export = ExportReceipt(
                        False, None, (), (), failure_code="export_adapter_exception"
                    )
                try:
                    cleanup = cleanup_resources(resources, process_stopped, lane)
                except BaseException:  # noqa: BLE001 - finalizer must emit conservative facts
                    cleanup = dict.fromkeys(empty_cleanup(), False)
        if deferred:
            strongest = max(deferred)
            status, reason, child_exit = "interrupted", "signal", 128 + strongest
        if not cleanup_passed(cleanup, resources_acquired=any(ownership.values())):
            status, reason = "failed", "cleanup_failed"
        export_failed = not export.secret_scan_passed or not runner_receipts_published
        if export_failed and resources is not None:
            status = "failed"
            if reason in {None, "not_started"}:
                reason = "artifact_export_failed"
        if lane == "live" and status == "passed" and not live_egress_passed(egress):
            status, reason = "failed", "egress_receipt_invalid"
    manifest = build_manifest(
        lane=lane,
        project=project,
        status=status,
        failure_reason=reason,
        child_exit_code=child_exit,
        self_test=self_test,
        selected_ids=selected_ids,
        executed_ids=executed_ids,
        facts=resource_facts(resources, run_id),
        export=export,
        egress=egress,
        ownership=ownership,
        cleanup=cleanup,
    )
    return manifest, child_exit if status == "interrupted" else (0 if status == "passed" else 1)


def main() -> int:
    return run_cli(_run)


if __name__ == "__main__":
    raise SystemExit(main())
