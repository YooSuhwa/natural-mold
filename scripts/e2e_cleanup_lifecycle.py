"""E2E provisioning and live-residue validation independent of artifact exports."""

from __future__ import annotations

import re
import socket
import subprocess

from cleanup_docker import probe_docker
from e2e_cleanup_contract import HEX_64, PORTS, PROJECTS, RUN_ID, require, string
from postgres_cleanup_checker import ManifestValidationError


def _validate_ownership(payload: dict[str, object], lane: str, status: str) -> bool:
    names = (
        "owned_run_root",
        "owned_database",
        "owned_backend",
        "owned_frontend",
        "owned_proxy",
    )
    require(all(isinstance(payload.get(name), bool) for name in names), "ownership")
    root, database, backend, frontend, proxy = (bool(payload[name]) for name in names)
    require(not database or root, "ownership")
    require(not backend or (database and frontend), "ownership")
    require(not frontend or (database and backend), "ownership")
    require(proxy is (lane == "live" and backend), "ownership")
    if status == "passed":
        require(root and database and backend and frontend, "ownership")
    return database


def validate_lifecycle(payload: dict[str, object]) -> tuple[str, str, bool]:
    """Validate lane/project, immutable provisioning facts, and ownership truthfulness."""
    lane = string(payload.get("lane"), "lane")
    require(lane in PROJECTS, "lane")
    project = string(payload.get("project"), "project")
    require(project in PROJECTS[lane], "project_lane")
    require(
        payload.get("workers") == 1
        and payload.get("retries") == 0
        and payload.get("reuse_existing_server") is False,
        "execution_policy",
    )
    require(RUN_ID.fullmatch(string(payload.get("run_id"), "run_id")) is not None, "run_id")
    require(payload.get("postgres_image") == "postgres:16-alpine", "postgres_image")
    require(
        payload.get("frontend_port") == PORTS[lane][0]
        and payload.get("backend_port") == PORTS[lane][1],
        "ports",
    )
    status = string(payload.get("status"), "status")
    database_owned = _validate_ownership(payload, lane, status)
    if database_owned:
        require(
            re.fullmatch(r"16\d{4}", string(payload.get("server_version_num"), "server_version"))
            is not None,
            "server_version",
        )
        head = string(payload.get("alembic_head"), "alembic")
        require(bool(head) and payload.get("alembic_current") == head, "alembic")
        require(
            HEX_64.fullmatch(string(payload.get("schema_fingerprint"), "schema_fingerprint"))
            is not None,
            "schema_fingerprint",
        )
        require(payload.get("second_upgrade_idempotent") is True, "alembic_idempotence")
    else:
        fields = (
            "server_version_num",
            "alembic_head",
            "alembic_current",
            "schema_fingerprint",
        )
        require(all(payload.get(name) is None for name in fields), "provisioning_facts")
        require(payload.get("second_upgrade_idempotent") is False, "provisioning_facts")
    return lane, project, database_owned


def validate_live_absence(payload: dict[str, object]) -> None:
    """Check actual fixed-port listeners and the logical owned Docker name."""
    lane = string(payload.get("lane"), "lane")
    run_id = string(payload.get("run_id"), "run_id")
    for port in PORTS[lane]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.25)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise ManifestValidationError("live_port")
    try:
        result = probe_docker(
            ("container", "inspect", f"moldy-e2e-{run_id}"),
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ManifestValidationError("live_container") from error
    require(result.returncode != 0, "live_container")
