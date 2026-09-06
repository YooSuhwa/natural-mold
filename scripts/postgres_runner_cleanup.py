"""Fail-closed cleanup for disposable PostgreSQL runner resources."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from postgres_runner_runtime import (
    OWNER_LABEL,
    OwnedContainer,
    cleanup_container,
    cleanup_run_root,
    docker_ids,
    process_groups_stopped,
    run_command,
    verify_container_absence,
)


@dataclass(frozen=True, slots=True)
class CleanupContext:
    owner: OwnedContainer
    owner_token: str
    run_root: Path | None
    root_device: int | None
    root_inode: int | None
    before_ids: set[str] | None


def cleanup_scenario_resources(context: CleanupContext) -> dict[str, object]:
    """Attempt every cleanup step and return conservative observations."""
    try:
        container_removed = cleanup_container(context.owner)
    except BaseException:  # noqa: BLE001 - teardown must continue after any interruption
        container_removed = False
    try:
        identity_absent, label_absent, port_absent = verify_container_absence(context.owner)
    except BaseException:  # noqa: BLE001 - teardown must continue after any interruption
        identity_absent, label_absent, port_absent = False, False, False
    try:
        label_query = run_command(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                f"label={OWNER_LABEL}={context.owner_token}",
            ],
            timeout=20,
        )
        label_absent = (
            label_absent and label_query.returncode == 0 and not label_query.stdout.strip()
        )
    except BaseException:  # noqa: BLE001 - teardown must continue after any interruption
        label_absent = False
    root_removed = context.run_root is None
    if (
        context.run_root is not None
        and context.root_device is not None
        and context.root_inode is not None
    ):
        try:
            root_removed = cleanup_run_root(
                context.run_root,
                context.root_device,
                context.root_inode,
            )
        except BaseException:  # noqa: BLE001 - teardown must continue after any interruption
            root_removed = False
    try:
        current_ids: set[str] | None = docker_ids()
    except BaseException:  # noqa: BLE001 - teardown must continue after any interruption
        current_ids = None
    try:
        groups_stopped = process_groups_stopped()
    except BaseException:  # noqa: BLE001 - teardown must complete with a false receipt
        groups_stopped = False
    before_observed = context.before_ids is not None
    foreign_preserved = (
        context.before_ids.issubset(current_ids)
        if context.before_ids is not None and current_ids is not None
        else None
    )
    return {
        "cleanup_container_removed": container_removed and identity_absent,
        "owned_label_absent": label_absent,
        "port_mapping_removed": port_absent,
        "process_group_stopped": groups_stopped,
        "cleanup_run_root_removed": root_removed,
        "foreign_containers_observed": before_observed,
        "foreign_containers_preserved": foreign_preserved,
    }
