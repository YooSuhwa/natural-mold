"""Fail-closed cleanup and artifact-export adapters for isolated E2E."""

from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path

from e2e_runner_contract import Lane, Project
from e2e_runner_process import ports_have_no_listener
from e2e_runner_runtime import REPO_ROOT, E2eResources
from postgres_runner_runtime import (
    OWNER_LABEL,
    cleanup_container,
    cleanup_run_root,
    docker_ids,
    run_command,
    verify_container_absence,
)


def publish_runner_receipts(resources: E2eResources, project: Project) -> bool:
    """Copy runner-owned receipts into the export allowlisted results subtree."""
    source = resources.run_root / "runner"
    destination = resources.run_root / "frontend/test-results" / project
    try:
        try:
            source_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return True
        destination_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for name in (
                "selection.json",
                "selection.log",
                "execution.log",
                "execution.stderr.log",
            ):
                _copy_receipt_file(source_fd, destination_fd, name)
        finally:
            os.close(source_fd)
            os.close(destination_fd)
    except OSError:
        return False
    return True


def _copy_receipt_file(source_fd: int, destination_fd: int, name: str) -> None:
    try:
        input_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=source_fd)
    except FileNotFoundError:
        return
    try:
        metadata = os.fstat(input_fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise OSError("unsafe runner receipt")
        output_fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=destination_fd,
        )
        try:
            while chunk := os.read(input_fd, 1024 * 1024):
                view = memoryview(chunk)
                while view:
                    written = os.write(output_fd, view)
                    if written == 0:
                        raise OSError("short runner receipt write")
                    view = view[written:]
        finally:
            os.close(output_fd)
    finally:
        os.close(input_fd)


def cleanup_resources(
    resources: E2eResources, process_stopped: bool, lane: Lane
) -> dict[str, bool | None]:
    try:
        container_removed = cleanup_container(resources.owner)
    except BaseException:  # noqa: BLE001 - teardown must continue after interruption
        container_removed = False
    try:
        identity_absent, label_absent, port_absent = verify_container_absence(resources.owner)
    except BaseException:  # noqa: BLE001 - teardown must continue after interruption
        identity_absent, label_absent, port_absent = False, False, False
    try:
        label_query = run_command(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                f"label={OWNER_LABEL}={resources.owner.owner_token}",
            ],
            timeout=20,
        )
        label_absent = (
            label_absent and label_query.returncode == 0 and not label_query.stdout.strip()
        )
    except BaseException:  # noqa: BLE001 - teardown must continue after interruption
        label_absent = False
    try:
        run_root_removed = cleanup_run_root(
            resources.run_root, resources.root_device, resources.root_inode
        )
    except BaseException:  # noqa: BLE001 - teardown must continue after interruption
        run_root_removed = False
    try:
        current = docker_ids()
        foreign_preserved: bool | None = resources.before_containers.issubset(current)
    except BaseException:  # noqa: BLE001 - teardown must return conservative facts
        foreign_preserved = None
    ports = (3100, 8101) if lane == "scripted" else (3200, 8201)
    lane_ports_removed = _wait_for_ports_removed(ports)
    return {
        "cleanup_container_removed": container_removed and identity_absent,
        "owned_label_absent": label_absent,
        "postgres_port_removed": port_absent,
        "owned_database_removed": container_removed and identity_absent,
        "backend_port_removed": lane_ports_removed,
        "frontend_port_removed": lane_ports_removed,
        "proxy_port_removed": process_stopped,
        "process_group_stopped": process_stopped,
        "cleanup_run_root_removed": run_root_removed,
        "foreign_containers_preserved": foreign_preserved,
    }


def _wait_for_ports_removed(ports: tuple[int, ...]) -> bool:
    deadline = time.monotonic() + 5
    while True:
        if ports_have_no_listener(ports):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def cleanup_partial_run_root(run_root: Path, device: int, inode: int) -> bool:
    helper = REPO_ROOT / "scripts/cleanup-isolated-root.py"
    result = run_command(
        [sys.executable, str(helper), "cleanup", str(run_root), str(device), str(inode)],
        timeout=30,
    )
    return result.returncode == 0
