"""Sequential executor for canonical composite project gates."""

from __future__ import annotations

import secrets
import signal
from dataclasses import dataclass
from pathlib import Path
from types import FrameType

from project_gate_catalog import CATALOG
from project_gate_manifest import AggregateWriter
from project_gate_process import (
    GateSignal,
    command_for,
    run_process,
    safe_environment,
    validate_child,
)
from project_gate_receipts import ReceiptSummary, new_child_path
from project_gate_runtime import JSONObject, JSONValue, ProjectGateError
from project_gate_toolchain import (
    RepositoryProvenance,
    TrustedToolchain,
    verify_provenance,
    verify_toolchain,
)


@dataclass(frozen=True, slots=True)
class NodeOutcome:
    node_id: str
    status: str
    exit_code: int | None
    receipt: ReceiptSummary | None


def _receipt_payload(receipt: ReceiptSummary | None) -> JSONObject | None:
    if receipt is None:
        return None
    return {
        "relative_path": receipt["relative_path"],
        "sha256": receipt["sha256"],
        "cleanup_passed": receipt["cleanup_passed"],
        "secret_scan_passed": receipt["secret_scan_passed"],
        "workers": receipt["workers"],
        "retries": receipt["retries"],
        "screenshot_count": receipt["screenshot_count"],
    }


def _payload(
    profile: str,
    provenance: RepositoryProvenance,
    expected: tuple[str, ...],
    outcomes: list[NodeOutcome],
    runtime: dict[str, str] | None,
    failure: str | None,
    attempt_id: str | None = None,
) -> JSONObject:
    by_id = {outcome.node_id: outcome for outcome in outcomes}
    nodes: list[JSONValue] = []
    for node_id in expected:
        outcome = by_id.get(node_id)
        nodes.append(
            {
                "node_id": node_id,
                "status": outcome.status if outcome is not None else "not_run",
                "exit_code": outcome.exit_code if outcome is not None else None,
                "receipt": _receipt_payload(outcome.receipt) if outcome is not None else None,
            }
        )
    passed = (
        failure is None
        and len(outcomes) == len(expected)
        and all(outcome.status == "passed" for outcome in outcomes)
    )
    runtime_payload: JSONObject | None = None
    if runtime is not None:
        runtime_payload = {}
        for key, value in runtime.items():
            runtime_payload[key] = value
    expected_node_ids: list[JSONValue] = []
    expected_node_ids.extend(expected)
    executed_node_ids: list[JSONValue] = []
    executed_node_ids.extend(outcome.node_id for outcome in outcomes)
    return {
        "schema_version": 1,
        "runner": "moldy-composite-project-gate",
        "profile": profile,
        "base_sha": provenance.base_sha,
        "head_sha": provenance.head_sha,
        **({"attempt_id": attempt_id} if attempt_id is not None else {}),
        "status": "passed" if passed else "failed",
        "failure_reason": failure,
        "runtime": runtime_payload,
        "expected_node_ids": expected_node_ids,
        "executed_node_ids": executed_node_ids,
        "nodes": nodes,
        "cleanup_passed": all(
            outcome.receipt is not None and outcome.receipt["cleanup_passed"]
            for outcome in outcomes
        ),
        "secret_scan_passed": next(
            (
                outcome.receipt["secret_scan_passed"]
                for outcome in outcomes
                if outcome.receipt is not None and outcome.receipt["secret_scan_passed"] is not None
            ),
            None,
        ),
    }


def run_composite(
    profile: str,
    expected: tuple[str, ...],
    provenance: RepositoryProvenance,
    toolchain: TrustedToolchain,
    runtime: dict[str, str],
    manifest: Path,
    repo_root: Path,
    inherited: dict[str, str],
) -> int:
    """Run one canonical wave and always finalize its reserved aggregate receipt."""
    writer = AggregateWriter.create(manifest, repo_root, provenance.head_sha)
    environment = safe_environment(inherited, toolchain)
    outcomes: list[NodeOutcome] = []
    failure: str | None = None
    signal_exit: int | None = None
    previous_interrupt = signal.getsignal(signal.SIGINT)
    previous_terminate = signal.getsignal(signal.SIGTERM)
    signal_received = False

    def on_signal(number: int, _frame: FrameType | None) -> None:
        nonlocal signal_received
        if signal_received:
            return
        signal_received = True
        raise GateSignal(number)

    try:
        signal.signal(signal.SIGINT, on_signal)
        signal.signal(signal.SIGTERM, on_signal)
        for node_id in expected:
            verify_toolchain(toolchain)
            node = CATALOG[node_id]
            receipt = new_child_path(manifest, node_id, secrets.token_hex(8))
            command, extra = command_for(
                node,
                receipt,
                repo_root,
                toolchain,
                attempt_id=writer.attempt_id,
                head_sha=writer.head_sha,
            )
            try:
                verify_provenance(provenance, repo_root)
                writer.verify_binding()
                exit_code = run_process(command, repo_root, environment | extra)
            except GateSignal as interrupted:
                try:
                    if writer.attempt_id is None:
                        summary = validate_child(node, receipt, repo_root, 143, environment | extra)
                    else:
                        summary = validate_child(
                            node,
                            receipt,
                            repo_root,
                            143,
                            environment | extra,
                            writer.parent_descriptor,
                        )
                except ProjectGateError:
                    outcomes.append(NodeOutcome(node_id, "invalid_receipt", 143, None))
                else:
                    outcomes.append(NodeOutcome(node_id, "interrupted", 143, summary))
                failure = f"signal_{interrupted.number}"
                signal_exit = 128 + interrupted.number
                try:
                    verify_provenance(provenance, repo_root)
                except ProjectGateError:
                    failure = "repository_changed"
                break
            try:
                if writer.attempt_id is None:
                    summary = validate_child(
                        node, receipt, repo_root, exit_code, environment | extra
                    )
                else:
                    summary = validate_child(
                        node,
                        receipt,
                        repo_root,
                        exit_code,
                        environment | extra,
                        writer.parent_descriptor,
                    )
            except ProjectGateError:
                outcomes.append(NodeOutcome(node_id, "invalid_receipt", exit_code, None))
                failure = "invalid_child_receipt"
                try:
                    verify_provenance(provenance, repo_root)
                except ProjectGateError:
                    failure = "repository_changed"
                break
            status = "passed" if exit_code == 0 else "failed"
            outcomes.append(NodeOutcome(node_id, status, exit_code, summary))
            try:
                verify_provenance(provenance, repo_root)
                verify_toolchain(toolchain)
            except ProjectGateError as error:
                failure = str(error)
                break
            if exit_code != 0:
                failure = "child_failed"
                break
    except GateSignal as interrupted:
        failure = f"signal_{interrupted.number}"
        signal_exit = 128 + interrupted.number
    except ProjectGateError as error:
        failure = str(error)
    finally:
        if failure is None:
            try:
                verify_provenance(provenance, repo_root)
                verify_toolchain(toolchain)
            except ProjectGateError as error:
                failure = str(error)
        # Final receipt bytes and directory durability form one short critical
        # section. A late second signal must not leave a partial aggregate.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            writer.write(
                _payload(
                    profile,
                    provenance,
                    expected,
                    outcomes,
                    runtime,
                    failure,
                    writer.attempt_id,
                )
            )
        finally:
            writer.close()
            signal.signal(signal.SIGINT, previous_interrupt)
            signal.signal(signal.SIGTERM, previous_terminate)
    if signal_exit is not None:
        return signal_exit
    return 0 if failure is None and len(outcomes) == len(expected) else 1
