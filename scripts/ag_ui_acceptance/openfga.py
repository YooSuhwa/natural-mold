"""A loopback-only OpenFGA store shared by the two chat acceptance workers."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

_IMAGE = "openfga/openfga:v1.22.0"
_OWNER = "dev.moldy.ag-ui-openfga"


@contextmanager
def acceptance_openfga(backend_root: Path) -> Iterator[dict[str, str]]:
    """Start a private in-memory store, installing this checkout's authorization model."""
    model = (backend_root / "app/authz/model.json").read_bytes()
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("OpenFGA acceptance requires a local Docker daemon")
    host = os.environ.get("DOCKER_HOST")
    if not host or os.environ.get("DOCKER_CONTEXT"):
        host = subprocess.check_output(
            [docker, "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
            text=True,
            timeout=10,
        ).strip()
    if not host.startswith("unix:///"):
        raise RuntimeError("OpenFGA acceptance requires a local Docker Unix socket")

    owner = uuid.uuid4().hex
    name = f"moldy-agui-openfga-{owner}"
    key = secrets.token_hex(32)

    def command(*args: str) -> str:
        return subprocess.run(
            [docker, "--host", host, *args],
            env={**os.environ, "OPENFGA_AUTHN_PRESHARED_KEYS": key},
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()

    try:
        container = command(
            "create",
            "--pull=never",
            "--name",
            name,
            "--label",
            f"{_OWNER}={owner}",
            "--publish",
            "127.0.0.1::8080",
            "--env",
            "OPENFGA_AUTHN_PRESHARED_KEYS",
            _IMAGE,
            "run",
            "--datastore-engine=memory",
            "--playground-enabled=false",
            "--authn-method=preshared",
        )
        command("start", container)
        binding = command("port", container, "8080/tcp")
        if not re.fullmatch(r"127\.0\.0\.1:[0-9]+", binding):
            raise RuntimeError("OpenFGA acceptance port was not published on loopback")
        url = f"http://{binding}"
        client = build_opener(ProxyHandler({}))
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        ready = Request(f"{url}/healthz", headers=headers)
        deadline = time.monotonic() + 30
        while True:
            try:
                with client.open(ready, timeout=2) as response:
                    if response.status == 200:
                        break
            except (URLError, OSError):
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError("private OpenFGA failed readiness")
            time.sleep(0.1)

        def create(path: str, body: bytes, id_field: str) -> str:
            with client.open(
                Request(url + path, data=body, headers=headers), timeout=10
            ) as response:
                identifier = json.load(response)[id_field]
            if not isinstance(identifier, str) or not re.fullmatch(r"[0-9A-Z]{26}", identifier):
                raise RuntimeError(f"OpenFGA returned an invalid {id_field}")
            return identifier

        store = create("/stores", json.dumps({"name": name}).encode(), "id")
        model_id = create(f"/stores/{store}/authorization-models", model, "authorization_model_id")
        yield {
            "OPENFGA_API_URL": url,
            "OPENFGA_API_TOKEN": key,
            "OPENFGA_STORE_ID": store,
            "OPENFGA_MODEL_ID": model_id,
            "AUTHZ_MODE": "enforce",
            "AUTHZ_ENFORCE_TYPES": "organization,agent",
        }
    finally:
        # Also finds an owned container if create timed out before returning its ID.
        ids = command(
            "ps",
            "--all",
            "--no-trunc",
            "--quiet",
            "--filter",
            f"label={_OWNER}={owner}",
        )
        for identifier in ids.splitlines():
            if re.fullmatch(r"[0-9a-f]{64}", identifier):
                command("rm", "-f", identifier)
