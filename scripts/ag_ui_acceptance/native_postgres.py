"""A private PostgreSQL 16 instance for multi-worker chat acceptance tests."""

from __future__ import annotations

import os
import secrets
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

_ADMIN = "moldy_gate_admin"
_APP = "moldy_gate_web"


@dataclass(frozen=True)
class NativePostgres:
    name: str
    port: int
    data_dir: Path
    database_url: str = field(repr=False)
    database_url_sync: str = field(repr=False)
    migration_database_url: str = field(repr=False)
    admin_database_url: str = field(repr=False)

    def assert_application_access(self, *, rls: bool = True) -> None:
        """Ensure the real migration granted DML while reserving ownership for admin."""
        import psycopg

        with (
            psycopg.connect(
                self.database_url_sync,
                connect_timeout=4,
                options="-c statement_timeout=5000",
            ) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("""
                SELECT current_user, r.rolsuper, r.rolbypassrls,
                       has_schema_privilege(current_user, 'public', 'CREATE')
                FROM pg_roles r WHERE r.rolname = current_user
            """)
            if cursor.fetchone() != (_APP, False, False, False):
                raise RuntimeError("Temporary app role can bypass the intended permissions")
            cursor.execute("""
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                       pg_has_role(c.relowner, 'MEMBER') AS owns_table,
                       bool_and(has_table_privilege(c.oid, required.privilege)) AS can_write
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE'])
                           AS required(privilege)
                WHERE n.nspname = 'public' AND c.relname IN
                    ('conversations', 'conversation_runs', 'checkpoint_writes', 'apscheduler_jobs')
                GROUP BY c.oid
            """)
            rows = cursor.fetchall()
            if len(rows) != 4 or any(owns or not dml for _, _, _, owns, dml in rows):
                raise RuntimeError("Migration did not supply the expected app DML privileges")
            for name, enabled, forced, _, _ in rows:
                if rls and name == "conversations" and not (enabled and forced):
                    raise RuntimeError("Migration did not enforce conversation tenant isolation")


def _pg16_bin(env: dict[str, str]) -> Path:
    if explicit := os.environ.get("MOLDY_PG16_BINDIR"):
        candidates = [Path(explicit)]
    else:
        candidates = []
        if pg_config := shutil.which("pg_config"):
            bindir = subprocess.check_output(
                [pg_config, "--bindir"], env=env, text=True, timeout=5
            ).strip()
            candidates.append(Path(bindir))
        candidates.extend(
            Path(path)
            for path in (
                "/usr/lib/postgresql/16/bin",
                "/opt/homebrew/opt/postgresql@16/bin",
                "/usr/local/opt/postgresql@16/bin",
            )
        )
    for directory in candidates:
        if (directory / "postgres").is_file():
            version = subprocess.check_output(
                [directory / "postgres", "--version"], env=env, text=True, timeout=5
            )
            if version.split()[-1].split(".")[0] == "16" or " 16." in version:
                return directory
    raise RuntimeError("Set MOLDY_PG16_BINDIR to a PostgreSQL 16 bin directory")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _stop(server: subprocess.Popen[bytes]) -> None:
    for sig, timeout in ((signal.SIGINT, 10), (signal.SIGQUIT, 5), (signal.SIGKILL, 5)):
        if server.poll() is not None:
            return
        server.send_signal(sig)
        try:
            server.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            continue
    raise RuntimeError("Temporary acceptance PostgreSQL refused to terminate")


@contextmanager
def native_postgres() -> Iterator[NativePostgres]:
    """Yield isolated app and migration DSNs and remove only this process's cluster."""
    env = {"PATH": os.defpath, "LC_ALL": "C"}
    binaries = _pg16_bin(env)
    # Private socket paths must fit sockaddr_un even on macOS.
    with tempfile.TemporaryDirectory(prefix="moldy-agui-pg-", dir="/tmp") as root:
        folder = Path(root)
        data = folder / "data"
        socket_dir = folder / "sock"
        socket_dir.mkdir(mode=0o700)
        env["HOME"] = str(folder)
        subprocess.run(
            [
                binaries / "initdb",
                "-D",
                data,
                "-U",
                _ADMIN,
                "-E",
                "UTF8",
                "--no-locale",
                "--auth-local=trust",
                "--auth-host=scram-sha-256",
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
            timeout=45,
        )
        port = _free_port()
        name = f"moldy_e2e_scripted_agui_{port}"
        with (folder / "postgres.log").open("wb") as log:
            server = subprocess.Popen(
                [
                    binaries / "postgres",
                    "-D",
                    data,
                    "-h",
                    "127.0.0.1",
                    "-p",
                    str(port),
                    "-k",
                    str(socket_dir),
                    "-c",
                    "unix_socket_permissions=0700",
                    "-c",
                    "track_commit_timestamp=on",
                ],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            try:
                admin_flags = [
                    "-h",
                    str(socket_dir),
                    "-p",
                    str(port),
                    "-U",
                    _ADMIN,
                    "-d",
                    "postgres",
                ]
                deadline = time.monotonic() + 40
                while True:
                    if server.poll() is not None:
                        raise RuntimeError("Temporary acceptance PostgreSQL failed to start")
                    if (
                        subprocess.run(
                            [binaries / "pg_isready", *admin_flags, "-q", "-t", "1"],
                            env=env,
                            timeout=3,
                            check=False,
                        ).returncode
                        == 0
                    ):
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Temporary acceptance PostgreSQL readiness timed out")
                    time.sleep(0.1)

                password = secrets.token_hex(24)
                # Secrets travel on stdin; the command line and error messages omit them.
                sql = (
                    f"CREATE ROLE {_APP} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{password}';\n"
                    f"CREATE DATABASE {name} ENCODING 'UTF8';\n"
                )
                created = subprocess.run(
                    [
                        binaries / "psql",
                        "-X",
                        "-q",
                        "-v",
                        "ON_ERROR_STOP=1",
                        *admin_flags,
                    ],
                    input=sql,
                    text=True,
                    env=env,
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
                if created.returncode:
                    raise RuntimeError(
                        "Unable to provision temporary application role and database"
                    )
                app = f"{_APP}:{password}@127.0.0.1:{port}/{name}"
                admin = f"{_ADMIN}@/{name}?host={socket_dir}&port={port}"
                yield NativePostgres(
                    name=name,
                    port=port,
                    data_dir=data,
                    database_url=f"postgresql+asyncpg://{app}",
                    database_url_sync=f"postgresql://{app}",
                    migration_database_url=f"postgresql+asyncpg://{admin}",
                    admin_database_url=f"postgresql://{admin}",
                )
            finally:
                _stop(server)
