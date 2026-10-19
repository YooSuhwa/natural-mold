"""Own an ephemeral FGA process inside the disposable PostgreSQL test lane."""

import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest


@pytest.fixture(scope="module")
def openfga_url() -> Iterator[str]:
    """Foreground Docker joins pytest's process group and removes itself on exit."""
    owner = uuid.uuid4().hex
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("docker_required_for_authz_integration")
    name = f"moldy-fga-test-{owner}"
    label = "dev.moldy.authz-test-owner"
    run_root = (
        Path(os.environ["MOLDY_TEST_RUN_ROOT"]) if "MOLDY_TEST_RUN_ROOT" in os.environ else None
    )
    with tempfile.TemporaryFile(dir=run_root) as log:
        process = subprocess.Popen(  # noqa: S603 — trusted fixed image and generated test identity
            [
                docker,
                "run",
                "--rm",
                "--name",
                name,
                "--label",
                f"{label}={owner}",
                "-p",
                "127.0.0.1::8080",
                "openfga/openfga:v1.22.0",
                "run",
                "--datastore-engine=memory",
                "--playground-enabled=false",
                "--authn-method=preshared",
                "--authn-preshared-keys=p0-sdk-local-dummy",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            # Inherit the canonical runner's owned process group. SIGTERM to the
            # lane also reaches Docker, which forwards it to FGA (--rm).
        )
        try:
            import time

            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                result = subprocess.run(  # noqa: S603 — generated, owned container only
                    [docker, "port", name, "8080/tcp"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                match = re.fullmatch(r"127\.0\.0\.1:(\d+)\n?", result.stdout)
                if result.returncode == 0 and match is not None:
                    url = f"http://127.0.0.1:{match.group(1)}"
                    try:
                        with httpx.Client(base_url=url, timeout=1) as probe:
                            ready = probe.get("/healthz").status_code == 200
                    except httpx.TransportError:
                        ready = False
                    if ready:
                        yield url
                        return
                if process.poll() is not None:
                    raise RuntimeError("openfga_test_start_failed")
                time.sleep(0.1)
            raise RuntimeError("openfga_test_port_timeout")
        finally:
            identity = subprocess.run(  # noqa: S603 — verify ownership before deleting
                [docker, "inspect", "--format", f'{{{{index .Config.Labels "{label}"}}}}', name],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if identity.returncode == 0:
                if identity.stdout.strip() != owner:
                    raise RuntimeError("openfga_test_cleanup_owner_mismatch")
                subprocess.run(
                    [docker, "rm", "-f", name], check=True, capture_output=True, timeout=10
                )  # noqa: S603
            process.wait(timeout=10)
