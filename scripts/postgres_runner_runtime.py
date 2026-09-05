"""Process, Docker, migration, and pytest adapters for the PostgreSQL runner."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

import psycopg
from postgres_runner_contract import LaneDsns, parse_lane_dsns
from postgres_runner_process import process_groups_stopped as process_groups_stopped
from postgres_runner_process import process_identity_sha256 as process_identity_sha256
from postgres_runner_process import run_command as run_command
from postgres_runner_process import start_process_scope as start_process_scope

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
BACKEND_ROOT: Final = REPO_ROOT / "backend"
OWNER_LABEL: Final = "dev.moldy.postgres-test-owner"
IMAGE: Final = "postgres:16-alpine"
ExternalScenarioKind = Literal[
    "all", "migration-roundtrip", "stream-resume", "run-lifecycle+stream-resume"
]
ScenarioKind = Literal[
    "all",
    "migration-roundtrip",
    "stream-resume",
    "run-lifecycle+stream-resume",
    "success",
    "child_failure",
    "sigint",
]


@dataclass(slots=True)
class OwnedContainer:
    owner_token: str
    run_id: str
    name: str
    container_id: str | None = None
    port: int | None = None


class RunnerError(RuntimeError):
    """Stable runner failure without credential-bearing input."""


def docker_ids() -> set[str]:
    result = run_command(["docker", "ps", "-aq", "--no-trunc"], timeout=20)
    if result.returncode != 0:
        raise RunnerError("docker_unavailable")
    return {line for line in result.stdout.splitlines() if line}


def inspect_identity(container_id: str) -> tuple[str, str] | None:
    result = run_command(
        [
            "docker",
            "inspect",
            "--format",
            f'{{{{.Id}}}} {{{{index .Config.Labels "{OWNER_LABEL}"}}}}',
            container_id,
        ],
        timeout=20,
    )
    if result.returncode != 0:
        return None
    parts = result.stdout.strip().split(" ", 1)
    return (parts[0], parts[1] if len(parts) == 2 else "")


def cleanup_container(owner: OwnedContainer) -> bool:
    if owner.container_id is None:
        resolved = run_command(["docker", "inspect", "--format", "{{.Id}}", owner.name], timeout=20)
        if resolved.returncode != 0:
            return True
        owner.container_id = resolved.stdout.strip()
    if inspect_identity(owner.container_id) != (owner.container_id, owner.owner_token):
        return False
    result = run_command(["docker", "rm", "-f", owner.container_id], timeout=30)
    return result.returncode == 0 and inspect_identity(owner.container_id) is None


def verify_container_absence(owner: OwnedContainer) -> tuple[bool, bool, bool]:
    if owner.container_id is None:
        return True, True, True
    identity_absent = inspect_identity(owner.container_id) is None
    labeled = run_command(
        ["docker", "ps", "-aq", "--filter", f"label={OWNER_LABEL}={owner.owner_token}"],
        timeout=20,
    )
    label_absent = labeled.returncode == 0 and owner.container_id not in labeled.stdout.splitlines()
    port = run_command(["docker", "port", owner.container_id, "5432/tcp"], timeout=20)
    port_absent = port.returncode != 0
    return identity_absent, label_absent, port_absent


def cleanup_run_root(run_root: Path, device: int, inode: int) -> bool:
    helper = REPO_ROOT / "scripts/cleanup-isolated-root.py"
    result = run_command(
        [
            sys.executable,
            str(helper),
            "cleanup",
            str(run_root),
            str(device),
            str(inode),
        ],
        timeout=30,
    )
    return result.returncode == 0 and result.stdout.strip() == "removed"


def mapped_port(container_id: str) -> int:
    result = run_command(["docker", "port", container_id, "5432/tcp"], timeout=20)
    match = re.fullmatch(r"127\.0\.0\.1:(\d+)\n?", result.stdout)
    if result.returncode != 0 or match is None:
        raise RunnerError("unsafe_port_mapping")
    return int(match.group(1))


def inspect_storage(container_id: str, owner_token: str) -> tuple[bool, bool]:
    result = run_command(["docker", "inspect", container_id], timeout=20)
    if result.returncode != 0:
        return False, False
    decoded = json.loads(result.stdout)
    if not isinstance(decoded, list) or len(decoded) != 1:
        return False, False
    details = decoded[0]
    tmpfs = details.get("HostConfig", {}).get("Tmpfs", {})
    labels = details.get("Config", {}).get("Labels", {})
    ports = details.get("NetworkSettings", {}).get("Ports", {}).get("5432/tcp")
    storage_owned = "/var/lib/postgresql/data" in tmpfs
    identity_owned = labels.get(OWNER_LABEL) == owner_token
    loopback_port = (
        isinstance(ports, list) and len(ports) == 1 and ports[0].get("HostIp") == "127.0.0.1"
    )
    return storage_owned and identity_owned, loopback_port


def build_docker_run_argv(owner: OwnedContainer) -> list[str]:
    return [
        "docker",
        "run",
        "-d",
        "--name",
        owner.name,
        "--label",
        f"{OWNER_LABEL}={owner.owner_token}",
        "--tmpfs",
        "/var/lib/postgresql/data:rw,noexec,nosuid,size=512m",
        "-p",
        "127.0.0.1::5432",
        "-e",
        "POSTGRES_USER=runner",
        "-e",
        "POSTGRES_PASSWORD",
        "-e",
        "POSTGRES_DB=postgres",
        IMAGE,
    ]


def build_docker_env(password: str) -> dict[str, str]:
    return {"POSTGRES_PASSWORD": password}


def wait_ready(container_id: str, dsn: str) -> str:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        ready = run_command(
            [
                "docker",
                "exec",
                container_id,
                "pg_isready",
                "-U",
                "runner",
                "-d",
                "postgres",
            ],
            timeout=5,
        )
        if ready.returncode == 0:
            try:
                with psycopg.connect(dsn, connect_timeout=2) as connection:
                    version = connection.execute("SHOW server_version_num").fetchone()
                    if version is not None and str(version[0]).startswith("16"):
                        return str(version[0])
            except psycopg.Error:
                pass
        time.sleep(0.2)
    raise RunnerError("postgres_not_ready")


def _fingerprint(dsn: str) -> str:
    query = """SELECT table_schema, table_name, column_name, data_type
               FROM information_schema.columns
               WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
               ORDER BY 1, 2, ordinal_position"""
    with psycopg.connect(dsn, connect_timeout=5) as connection:
        rows = connection.execute(query).fetchall()
    return hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest()


def alembic_state(env: dict[str, str]) -> tuple[str, str, str, bool]:
    executable = str(BACKEND_ROOT / ".venv/bin/alembic")
    heads = run_command([executable, "heads"], env=env)
    head_lines = [line.split()[0] for line in heads.stdout.splitlines() if "(head)" in line]
    if heads.returncode != 0 or len(head_lines) != 1:
        raise RunnerError("alembic_head_count")
    head = head_lines[0]
    if run_command([executable, "upgrade", "head"], env=env, timeout=180).returncode != 0:
        raise RunnerError("alembic_upgrade_failed")
    current = run_command([executable, "current"], env=env)
    if current.returncode != 0 or head not in current.stdout:
        raise RunnerError("alembic_current_mismatch")
    fingerprint = _fingerprint(env["DATABASE_URL_SYNC"])
    if run_command([executable, "upgrade", "head"], env=env, timeout=180).returncode != 0:
        raise RunnerError("alembic_second_upgrade_failed")
    second_current = run_command([executable, "current"], env=env)
    unchanged = second_current.returncode == 0 and head in second_current.stdout
    unchanged = unchanged and fingerprint == _fingerprint(env["DATABASE_URL_SYNC"])
    return head, head, fingerprint, unchanged


def alembic_migration_roundtrip(
    env: dict[str, str], expected_head: str, expected_fingerprint: str
) -> bool:
    """Downgrade one revision and restore the exact disposable-lane schema."""
    executable = str(BACKEND_ROOT / ".venv/bin/alembic")
    downgrade = run_command([executable, "downgrade", "-1"], env=env, timeout=180)
    if downgrade.returncode != 0:
        return False
    previous = run_command([executable, "current"], env=env)
    if previous.returncode != 0 or expected_head in previous.stdout:
        return False
    upgrade = run_command([executable, "upgrade", "head"], env=env, timeout=180)
    if upgrade.returncode != 0:
        return False
    current = run_command([executable, "current"], env=env)
    return (
        current.returncode == 0
        and expected_head in current.stdout
        and _fingerprint(env["DATABASE_URL_SYNC"]) == expected_fingerprint
    )


def lane_env(dsns: LaneDsns, run_root: Path, receipt: Path) -> dict[str, str]:
    allowed = {"PATH", "TMPDIR", "LANG", "LC_ALL", "TZ", "HOME", "USER", "LOGNAME"}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.update(
        {
            "MOLDY_DISABLE_ENV_FILE": "true",
            "PYTHON_DOTENV_DISABLED": "1",
            "MOLDY_TEST_RUN_ROOT": str(run_root),
            "DATABASE_URL": dsns.async_url,
            "DATABASE_URL_SYNC": dsns.sync_url,
            "INTEGRATION_DATABASE_URL": dsns.integration_url,
            "MOLDY_PG_TEST_RECEIPT": str(receipt),
            "CHECKPOINTER_POOL_MIN_SIZE": "1",
            "CHECKPOINTER_POOL_MAX_SIZE": "2",
        }
    )
    return env


def run_test_child(env: dict[str, str], kind: ScenarioKind) -> tuple[int, str]:
    commands = {
        "all": [
            str(BACKEND_ROOT / ".venv/bin/pytest"),
            "-q",
            "-p",
            "tests.postgres_execution_plugin",
            "tests",
            "-m",
            "integration",
        ],
        "stream-resume": [
            str(BACKEND_ROOT / ".venv/bin/pytest"),
            "-q",
            "-p",
            "tests.postgres_execution_plugin",
            "tests/integration/test_stream_resume.py",
            "-m",
            "integration",
        ],
        "run-lifecycle+stream-resume": [
            str(BACKEND_ROOT / ".venv/bin/pytest"),
            "-q",
            "-p",
            "tests.postgres_execution_plugin",
            "tests/integration/test_conversation_run_lifecycle.py",
            "tests/integration/test_stream_resume.py",
            "-m",
            "integration",
        ],
        "success": [sys.executable, "-c", "raise SystemExit(0)"],
        "child_failure": [sys.executable, "-c", "raise SystemExit(23)"],
        "sigint": [
            sys.executable,
            "-c",
            "import os,signal;os.kill(os.getpid(),signal.SIGINT)",
        ],
    }
    result = run_command(commands[kind], env=env, timeout=600)
    target = parse_lane_dsns(
        LaneDsns(
            env["DATABASE_URL"],
            env["DATABASE_URL_SYNC"],
            env["INTEGRATION_DATABASE_URL"],
        )
    )
    output = (result.stdout + result.stderr).replace(target.password, "[redacted]")
    return (
        130 if kind == "sigint" and result.returncode == -signal.SIGINT else result.returncode
    ), output
