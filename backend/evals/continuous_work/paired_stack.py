"""Temporary PostgreSQL 16 and Moldy API for the paired ordinary/goal workload.

Run from the backend environment. Install PostgreSQL 16 locally and put its binaries
on PATH, or point PGBIN at their directory. The backend child can only connect to the
throwaway cluster and the caller's loopback model fixture.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

import httpx

_BACKEND = Path(__file__).resolve().parents[2]
_ORG = str(uuid5(NAMESPACE_URL, "moldy/e2e-organization"))
_TENANT = str(uuid5(NAMESPACE_URL, "moldy/e2e-tenant"))
_EMAIL = "paired-evaluation@moldy.dev"
_LOCAL = "127.0.0.1"


@dataclass(frozen=True)
class PairedStack:
    client: httpx.AsyncClient
    agent_id: str
    postgres_dsn: str
    org_id: str
    tenant_id: str
    model_name: str


def _port() -> int:
    with socket.socket() as sock:
        sock.bind((_LOCAL, 0))
        return int(sock.getsockname()[1])


def _pg_bin() -> Path:
    candidates = [os.environ.get("PGBIN"), "/opt/homebrew/opt/postgresql@16/bin"]
    if command := shutil.which("pg_ctl"):
        candidates.append(str(Path(command).parent))
    for candidate in candidates:
        if candidate and (ctl := Path(candidate) / "pg_ctl").is_file():
            version = subprocess.check_output([ctl, "--version"], text=True)  # noqa: S603
            if "(PostgreSQL) 16." in version:
                return Path(candidate)
    raise RuntimeError("Set PGBIN to the directory containing PostgreSQL 16 binaries")


@contextmanager
def _cluster(root: Path, env: dict[str, str]) -> Iterator[tuple[str, int]]:
    binary = _pg_bin()
    data = root / "pg"
    port = _port()
    role = "goal_eval"
    database = "moldy_e2e_scripted_goal_compare"
    with (root / "pg-control.log").open("wb") as log:

        def pg(program: str, *args: str) -> None:
            proc = subprocess.run(  # noqa: S603 — local pg binaries, fixed argv
                [binary / program, *args], env=env, stdout=log, stderr=subprocess.STDOUT
            )
            if proc.returncode:
                raise RuntimeError(
                    f"{program} failed: {(root / 'pg-control.log').read_text()[-2000:]}"
                )

        pg("initdb", "-D", str(data), "-U", role, "-A", "trust", "-E", "UTF8", "--no-locale")
        pg(
            "pg_ctl",
            "-D",
            str(data),
            "-l",
            str(root / "postgres.log"),
            "-o",
            f"-p {port} -h {_LOCAL} -k '{root}'",
            "-w",
            "start",
        )
        try:
            pg("createdb", "-h", _LOCAL, "-p", str(port), "-U", role, database)
            yield f"postgresql://{role}@{_LOCAL}:{port}/{database}", port
        finally:
            pg("pg_ctl", "-D", str(data), "-w", "-m", "immediate", "stop")


def _serve(api_port: int, pg_port: int, model_port: int) -> None:
    permitted = {pg_port, model_port}

    def isolate(event: str, args: tuple[object, ...]) -> None:
        if event == "socket.getaddrinfo" and args[0] not in {_LOCAL, "localhost", "::1", None}:
            raise PermissionError("Evaluation does not permit external DNS lookups")
        if event == "socket.connect":
            peer = args[1]
            if not (isinstance(peer, tuple) and peer[0] == _LOCAL and peer[1] in permitted):
                raise PermissionError(
                    "Evaluation connection outside the fixture and PG is disabled"
                )

    sys.addaudithook(isolate)
    import uvicorn

    uvicorn.run("app.main:app", host=_LOCAL, port=api_port, access_log=False)


async def _seed(client: httpx.AsyncClient, model_name: str, password: str) -> str:
    login = await client.post("/api/auth/login", json={"email": _EMAIL, "password": password})
    login.raise_for_status()
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    models = await client.get("/api/models")
    models.raise_for_status()
    model = next(model for model in models.json() if model["model_name"] == model_name)
    priced = await client.patch(
        f"/api/models/{model['id']}",
        json={"cost_per_input_token": "0.000001", "cost_per_output_token": "0.000002"},
    )
    priced.raise_for_status()
    agent = await client.post(
        "/api/agents",
        json={
            "name": "Synthetic vendor comparison",
            "model_id": model["id"],
            "system_prompt": "Compare only the supplied quotations and write the requested CSV.",
            "runtime_policy": {"version": 1, "filesystem": {"mode": "artifact_write"}},
        },
    )
    agent.raise_for_status()
    return str(agent.json()["id"])


@asynccontextmanager
async def paired_stack(
    provider_base_url: str, model_name: str = "goal-evaluation"
) -> AsyncIterator[PairedStack]:
    endpoint = urlsplit(provider_base_url)
    if (
        endpoint.scheme != "http"
        or endpoint.hostname != _LOCAL
        or not endpoint.port
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
    ):
        raise ValueError("Supply an explicit http://127.0.0.1:PORT loopback model fixture")
    api_port = _port()
    with TemporaryDirectory(prefix="moldy-goal-pairs-") as directory:
        root = Path(directory)
        env = {
            "PATH": os.defpath,
            "HOME": str(root),
            "LANG": "C",
            "PYTHONPATH": str(_BACKEND),
            "MOLDY_DISABLE_ENV_FILE": "true",
            "MOLDY_TEST_RUN_ROOT": str(root),
        }
        with _cluster(root, env) as (dsn, pg_port):
            password = secrets.token_urlsafe(24)
            env.update(
                DATABASE_URL=dsn.replace("postgresql://", "postgresql+asyncpg://", 1),
                DATABASE_URL_SYNC=dsn,
                JWT_SECRET=secrets.token_hex(32),
                ENCRYPTION_KEYS=secrets.token_hex(32),
                E2E_SEED_USER_ENABLED="true",
                E2E_USER_EMAIL=_EMAIL,
                E2E_USER_PASSWORD=password,
                E2E_LLM_BASE_URL=provider_base_url,
                E2E_LLM_API_KEY="local-fixture",
                E2E_LLM_MODEL=model_name,
                CONVERSATION_GOAL_ORG_IDS=json.dumps([_ORG]),
                CHAT_STEER_ENABLED="true",
                RATE_LIMIT_ENABLED="false",
                CREDENTIAL_HTTP_ALLOWED_HOSTS=_LOCAL,
                OUTBOUND_PRIVATE_ALLOWED_HOSTS=_LOCAL,
            )
            with (root / "server.log").open("wb") as log:
                migration = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "alembic",
                    "-c",
                    str(_BACKEND / "alembic.ini"),
                    "upgrade",
                    "head",
                    cwd=root,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                if await migration.wait():
                    raise RuntimeError(
                        f"Migration failed: {(root / 'server.log').read_text()[-3000:]}"
                    )
                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "evals.continuous_work.paired_stack",
                    str(api_port),
                    str(pg_port),
                    str(endpoint.port),
                    cwd=root,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                try:
                    async with httpx.AsyncClient(
                        base_url=f"http://{_LOCAL}:{api_port}", timeout=60, trust_env=False
                    ) as client:
                        async with asyncio.timeout(120):
                            while True:
                                if proc.returncode is not None:
                                    tail = (root / "server.log").read_text()[-3000:]
                                    raise RuntimeError(f"Startup failed: {tail}")
                                try:
                                    if (await client.get("/api/health")).is_success:
                                        break
                                except httpx.ConnectError:
                                    pass
                                await asyncio.sleep(0.2)
                        agent_id = await _seed(client, model_name, password)
                        yield PairedStack(client, agent_id, dsn, _ORG, _TENANT, model_name)
                finally:
                    if proc.returncode is None:
                        proc.terminate()
                        try:
                            await asyncio.wait_for(proc.wait(), 12)
                        except TimeoutError:
                            proc.kill()
                            await proc.wait()


if __name__ == "__main__":
    _serve(int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]))
