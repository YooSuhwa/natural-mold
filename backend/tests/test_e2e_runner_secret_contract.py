"""Secret propagation tests for the isolated E2E runner."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from e2e_runner_contract import build_e2e_dsns  # noqa: E402
from e2e_runner_runtime import E2eResources  # noqa: E402
from e2e_runner_scenario import secrets_from_environment  # noqa: E402
from postgres_runner_runtime import OwnedContainer  # noqa: E402


def _resources(tmp_path: Path, password: str) -> E2eResources:
    """Build a resource fixture that keeps the raw password beside its encoded DSN."""
    run_id = "a" * 24
    return E2eResources(
        run_id=run_id,
        run_root=tmp_path,
        root_device=tmp_path.stat().st_dev,
        root_inode=tmp_path.stat().st_ino,
        owner=OwnedContainer("owner", run_id, "container", "id", 54321),
        before_containers=set(),
        dsns=build_e2e_dsns(
            password=password,
            port=54321,
            database=f"moldy_e2e_scripted_{run_id}",
        ),
        server_version="160001",
        alembic_head="m70",
        alembic_current="m70",
        schema_fingerprint="fingerprint",
        second_upgrade_idempotent=True,
        postgres_password=password,
    )


def test_generated_postgres_password_is_an_exact_scan_secret(tmp_path: Path) -> None:
    """Given resources, when scan secrets are assembled, then the raw password is preserved."""
    password = "raw:postgres/password-only"
    resources = _resources(tmp_path, password)
    environment = {
        "DATABASE_URL": resources.dsns.async_url,
        "DATABASE_URL_SYNC": resources.dsns.sync_url,
        "INTEGRATION_DATABASE_URL": resources.dsns.integration_url,
        "E2E_USER_PASSWORD": "e2e-user-password",
        "ENCRYPTION_KEYS": "encryption-key",
        "JWT_SECRET": "jwt-secret",
    }

    secrets = secrets_from_environment(
        environment,
        postgres_password=resources.postgres_password,
    )

    assert password in secrets
    assert secrets[-1] == password


def test_password_only_artifact_is_rejected_by_real_scanner(tmp_path: Path) -> None:
    """Given a password-only log, when scanned with runner secrets, then it is rejected."""
    password = "raw:postgres/password-only"
    resources = _resources(tmp_path, password)
    environment = {
        "DATABASE_URL": resources.dsns.async_url,
        "DATABASE_URL_SYNC": resources.dsns.sync_url,
        "INTEGRATION_DATABASE_URL": resources.dsns.integration_url,
        "E2E_USER_PASSWORD": "e2e-user-password",
        "ENCRYPTION_KEYS": "encryption-key",
        "JWT_SECRET": "jwt-secret",
    }
    artifact = tmp_path / "execution.log"
    artifact.write_text(password)
    scanner = Path(__file__).resolve().parents[2] / "frontend/scripts/e2e-artifact-secret-scan.mjs"
    script = (
        "import { readFileSync } from 'node:fs'; "
        f"import {{ scanArtifactContent }} from {json.dumps(scanner.as_uri())}; "
        "scanArtifactContent(readFileSync(process.env.ARTIFACT_PATH), "
        "JSON.parse(process.env.SECRETS_JSON));"
    )
    child_environment = {
        **os.environ,
        "ARTIFACT_PATH": str(artifact),
        "SECRETS_JSON": json.dumps(
            list(
                secrets_from_environment(
                    environment,
                    postgres_password=resources.postgres_password,
                )
            )
        ),
    }

    node = shutil.which("node")
    assert node is not None
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        env=child_environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
