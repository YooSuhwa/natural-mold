"""Sanitized process and child-receipt boundary for composite gates."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from contextlib import suppress
from pathlib import Path
from typing import Final, assert_never

from project_gate_catalog import GateNode
from project_gate_receipts import ReceiptSummary, validate_e2e, validate_postgres, validate_static
from project_gate_runtime import ProjectGateError
from project_gate_toolchain import TrustedToolchain

TERM_GRACE_SECONDS: Final = 2.0


class GateSignal(RuntimeError):
    """Signal translated into deterministic cleanup and aggregate finalization."""

    def __init__(self, number: int) -> None:
        self.number = number
        super().__init__(f"signal_{number}")


def safe_environment(inherited: dict[str, str], toolchain: TrustedToolchain) -> dict[str, str]:
    environment = {
        "PATH": toolchain.path,
        "HOME": str(toolchain.home),
        "TMPDIR": "/tmp",  # noqa: S108 - mktemp child roots are identity-bound and private
        "USER": toolchain.user,
        "LOGNAME": toolchain.user,
        "LC_ALL": "C",
        "MOLDY_GATE_PYTHON": str(toolchain.python),
        "npm_config_manage_package_manager_versions": "false",
    }
    for name in ("CI", "DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CONTEXT"):
        if name in inherited:
            environment[name] = inherited[name]
    return environment


def command_for(
    node: GateNode, receipt: Path, repo_root: Path, toolchain: TrustedToolchain
) -> tuple[list[str], dict[str, str]]:
    kind = node.kind
    match kind:
        case "isolated":
            executable = [str(toolchain.python)]
            if node.cwd == "frontend":
                executable = [str(toolchain.node), str(toolchain.pnpm)]
            command = [
                "/bin/bash",
                str(repo_root / "scripts" / "run-isolated-command.sh"),
                "--cwd",
                node.cwd,
                "--",
                *executable,
                *node.argv,
            ]
            extra = {"MOLDY_CLEANUP_MANIFEST": str(receipt)}
        case "postgres":
            command = [
                "/bin/bash",
                str(repo_root / "scripts" / "run-isolated-postgres-tests.sh"),
                node.argv[0],
                "--manifest",
                str(receipt),
            ]
            extra = {}
        case "e2e":
            command = [
                str(toolchain.node),
                str(toolchain.pnpm),
                "--dir",
                "frontend",
                "test:e2e:scripted",
                "--",
                f"--project={node.argv[0]}",
                *node.argv[1:],
            ]
            extra = {
                "E2E_RUN_MANIFEST": str(receipt),
                "E2E_EXPORT_SLUG": f"project-gate-{node.node_id}-{receipt.stem[-16:]}",
            }
        case _:
            assert_never(kind)
    return command, extra


def run_process(command: list[str], repo_root: Path, environment: dict[str, str]) -> int:
    try:
        child = subprocess.Popen(  # noqa: S603 - argv comes only from the fixed catalog
            command,
            cwd=repo_root,
            env=environment,
            start_new_session=True,
        )
    except OSError as error:
        raise ProjectGateError("child_start_failed") from error
    try:
        return child.wait()
    except GateSignal:
        previous_handlers = {
            number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)
        }
        # A second terminal signal must not interrupt the bounded TERM/KILL/reap
        # sequence and leave the owned process group behind.
        for number in previous_handlers:
            signal.signal(number, signal.SIG_IGN)
        try:
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGTERM)
            deadline = time.monotonic() + TERM_GRACE_SECONDS
            while not _process_group_absent(child.pid) and time.monotonic() < deadline:
                child.poll()
                time.sleep(0.02)
            if not _process_group_absent(child.pid):
                with suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
            try:
                child.wait(timeout=TERM_GRACE_SECONDS)
            except subprocess.TimeoutExpired as error:
                raise ProjectGateError("child_reap_failed") from error
            kill_deadline = time.monotonic() + TERM_GRACE_SECONDS
            while not _process_group_absent(child.pid) and time.monotonic() < kill_deadline:
                time.sleep(0.02)
            if not _process_group_absent(child.pid):
                raise ProjectGateError("child_reap_failed")
        finally:
            for number, handler in previous_handlers.items():
                signal.signal(number, handler)
        raise


def _process_group_absent(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _checker(receipt: Path, repo_root: Path, environment: dict[str, str]) -> bool:
    command = [
        str(repo_root / "backend" / ".venv" / "bin" / "python"),
        str(repo_root / "scripts" / "check-isolation-cleanup.py"),
        str(receipt),
    ]
    try:
        result = subprocess.run(  # noqa: S603 - fixed independent receipt checker
            command,
            cwd=repo_root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def validate_child(
    node: GateNode, receipt: Path, repo_root: Path, exit_code: int, environment: dict[str, str]
) -> ReceiptSummary:
    kind = node.kind
    match kind:
        case "isolated":
            return validate_static(receipt, repo_root, exit_code)
        case "postgres":
            if not _checker(receipt, repo_root, environment):
                raise ProjectGateError("invalid_child_receipt")
            return validate_postgres(receipt, repo_root, node.argv[0], exit_code)
        case "e2e":
            if not _checker(receipt, repo_root, environment):
                raise ProjectGateError("invalid_child_receipt")
            expected_spec = node.argv[1] if len(node.argv) == 2 else None
            return validate_e2e(
                receipt,
                repo_root,
                exit_code,
                project=node.argv[0],
                expected_spec=expected_spec,
            )
        case _:
            assert_never(kind)
