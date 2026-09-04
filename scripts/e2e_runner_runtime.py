"""Docker, migration, environment, proxy, and exporter adapters for E2E."""

from __future__ import annotations

import os
import secrets
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import psycopg
from e2e_runner_contract import E2eDsns, Lane, Project, build_e2e_dsns, parse_e2e_dsns
from e2e_runner_environment import build_lane_environment
from postgres_manifest_io import RunnerInterrupted
from postgres_runner_runtime import (
    OWNER_LABEL,
    OwnedContainer,
    alembic_state,
    build_docker_env,
    build_docker_run_argv,
    cleanup_container,
    cleanup_run_root,
    docker_ids,
    inspect_identity,
    inspect_storage,
    mapped_port,
    run_command,
    verify_container_absence,
    wait_ready,
)
from psycopg import sql

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
FRONTEND_ROOT: Final = REPO_ROOT / "frontend"


@dataclass(slots=True)
class E2eResources:
    run_id: str
    run_root: Path
    root_device: int
    root_inode: int
    owner: OwnedContainer
    before_containers: set[str]
    dsns: E2eDsns
    server_version: str
    alembic_head: str
    alembic_current: str
    schema_fingerprint: str
    second_upgrade_idempotent: bool
    postgres_password: str = ""


class ProvisioningError(RuntimeError):
    """Provisioning failed after fail-closed partial-resource cleanup."""

    def __init__(
        self,
        reason: str,
        cleanup: dict[str, bool | None],
        ownership: dict[str, bool],
        signal_number: int | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.cleanup = cleanup
        self.ownership = ownership
        self.signal_number = signal_number


def _prepare_run_root() -> tuple[Path, os.stat_result]:
    run_root = Path(tempfile.mkdtemp(prefix=".moldy-test-run.e2e-"))
    run_root.chmod(stat.S_IRWXU)
    root_stat = run_root.stat()
    try:
        prepared = run_command(
            [
                sys.executable,
                str(REPO_ROOT / "scripts/prepare-isolated-run.py"),
                str(run_root),
                str(REPO_ROOT),
            ],
            timeout=120,
        )
        if prepared.returncode != 0:
            raise RuntimeError("run_root_prepare_failed")
    except BaseException as error:
        try:
            root_removed = cleanup_run_root(run_root, root_stat.st_dev, root_stat.st_ino)
        except BaseException:  # noqa: BLE001 - fail-closed receipt
            root_removed = False
        cleanup = _partial_cleanup(root_removed=root_removed)
        raise ProvisioningError(
            type(error).__name__,
            cleanup,
            _partial_ownership(run_root=True, database=False),
            error.signal_number if isinstance(error, RunnerInterrupted) else None,
        ) from error
    return run_root, root_stat


def _partial_ownership(*, run_root: bool, database: bool) -> dict[str, bool]:
    return {
        "run_root": run_root,
        "database": database,
        "backend": False,
        "frontend": False,
        "proxy": False,
    }


def _partial_cleanup(
    *,
    root_removed: bool,
    container_removed: bool = True,
    label_absent: bool = True,
    port_absent: bool = True,
    foreign_preserved: bool | None = None,
) -> dict[str, bool | None]:
    return {
        "cleanup_container_removed": container_removed,
        "owned_label_absent": label_absent,
        "postgres_port_removed": port_absent,
        "owned_database_removed": container_removed,
        "backend_port_removed": True,
        "frontend_port_removed": True,
        "proxy_port_removed": True,
        "process_group_stopped": True,
        "cleanup_run_root_removed": root_removed,
        "foreign_containers_preserved": foreign_preserved,
    }


def provision_resources(lane: Lane, project: Project) -> E2eResources:
    run_id = secrets.token_hex(12)
    owner_token = secrets.token_hex(32)
    owner = OwnedContainer(owner_token, run_id, f"moldy-e2e-{run_id}")
    run_root, root_stat = _prepare_run_root()
    before: set[str] | None = None
    database_created = False
    try:
        before = docker_ids()
        password = secrets.token_urlsafe(24)
        created = run_command(
            build_docker_run_argv(owner), env=build_docker_env(password), timeout=60
        )
        if created.returncode != 0:
            raise RuntimeError("container_create_failed")
        owner.container_id = created.stdout.strip()
        if inspect_identity(owner.container_id) != (owner.container_id, owner_token):
            raise RuntimeError("container_identity_mismatch")
        storage_owned, loopback_mapped = inspect_storage(owner.container_id, owner_token)
        if not storage_owned or not loopback_mapped:
            raise RuntimeError("container_storage_or_port_mismatch")
        owner.port = mapped_port(owner.container_id)
        admin_dsn = build_e2e_dsns(
            password=password, port=owner.port, database=f"moldy_e2e_{lane}_admin"
        )
        postgres_url = admin_dsn.sync_url.rsplit("/", 1)[0] + "/postgres"
        server = wait_ready(owner.container_id, postgres_url)
        database = f"moldy_e2e_{lane}_{run_id}"
        dsns = build_e2e_dsns(password=password, port=owner.port, database=database)
        parse_e2e_dsns(dsns, lane)
        with psycopg.connect(postgres_url, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        database_created = True
        env = build_lane_environment(lane, project, dsns, run_root)
        head, current, fingerprint, idempotent = alembic_state(env)
        return E2eResources(
            run_id,
            run_root,
            root_stat.st_dev,
            root_stat.st_ino,
            owner,
            before,
            dsns,
            server,
            head,
            current,
            fingerprint,
            idempotent,
            postgres_password=password,
        )
    except BaseException as error:
        try:
            container_removed = cleanup_container(owner)
        except BaseException:  # noqa: BLE001 - partial cleanup must continue
            container_removed = False
        try:
            root_removed = cleanup_run_root(run_root, root_stat.st_dev, root_stat.st_ino)
        except BaseException:  # noqa: BLE001 - partial cleanup must return conservative facts
            root_removed = False
        try:
            foreign_preserved: bool | None = (
                before.issubset(docker_ids()) if before is not None else None
            )
        except BaseException:  # noqa: BLE001 - partial cleanup must return conservative facts
            foreign_preserved = None
        try:
            identity_absent, label_absent, port_absent = verify_container_absence(owner)
            label_query = run_command(
                ["docker", "ps", "-aq", "--filter", f"label={OWNER_LABEL}={owner_token}"],
                timeout=20,
            )
            label_absent = (
                label_absent and label_query.returncode == 0 and not label_query.stdout.strip()
            )
        except BaseException:  # noqa: BLE001 - fail-closed receipt
            identity_absent, label_absent, port_absent = False, False, False
        cleanup = _partial_cleanup(
            root_removed=root_removed,
            container_removed=container_removed and identity_absent,
            label_absent=label_absent,
            port_absent=port_absent,
            foreign_preserved=foreign_preserved,
        )
        ownership = _partial_ownership(
            run_root=True,
            database=database_created,
        )
        signal_number = error.signal_number if isinstance(error, RunnerInterrupted) else None
        raise ProvisioningError(type(error).__name__, cleanup, ownership, signal_number) from error
