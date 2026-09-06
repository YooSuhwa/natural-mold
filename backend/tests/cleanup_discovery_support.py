"""Shared fixtures for cleanup-discovery contract tests."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cleanup_discovery_claims as claim_parser  # noqa: E402
from cleanup_discovery_claims import JSONValue as JSONValue  # noqa: E402
from postgres_cleanup_checker import (  # noqa: E402
    ManifestValidationError as ManifestValidationError,
)

type JSONObject = dict[str, JSONValue]

__all__ = (
    "CHECKER",
    "JSONValue",
    "JSONObject",
    "ManifestValidationError",
    "claim_parser",
    "discovery",
    "e2e_payload",
    "postgres_payload",
    "write_payload",
)

CHECKER = SCRIPTS / "check-isolation-cleanup.py"
_SPEC = importlib.util.spec_from_file_location("cleanup_discovery_checker", CHECKER)
assert _SPEC is not None and _SPEC.loader is not None
discovery = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(discovery)


def write_payload(root: Path, name: str, payload: JSONObject) -> Path:
    path = root / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    return path


def e2e_payload(run_id: str = "a" * 24) -> JSONObject:
    return {
        "schema_version": 1,
        "runner": "moldy-isolated-e2e",
        "run_id": run_id,
        "lane": "scripted",
        "frontend_port": 3100,
        "backend_port": 8101,
    }


def postgres_payload(temp_parent: Path) -> JSONObject:
    return {
        "schema_version": 1,
        "mode": "all",
        "scenarios": [
            {
                "process_id": 12345,
                "process_identity_sha256": "c" * 64,
                "run_root_created": True,
                "run_root": str(temp_parent / ".moldy-test-run.recorded"),
                "port_mapping_observed": True,
                "port": 49152,
            }
        ],
    }


@pytest.fixture(name="absent_probes")
def absent_probes_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Return a private synthetic temp root whose external probes are inert."""
    system_temp = tmp_path / "system-temp"
    system_temp.mkdir(mode=0o700)
    monkeypatch.setattr(discovery, "SYSTEM_TEMP", system_temp)
    monkeypatch.setattr(discovery, "LEGACY_TEMP_PARENTS", ())
    monkeypatch.setattr(discovery, "_port_has_listener", lambda _port: False)

    def absent_docker(
        arguments: tuple[str, ...], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        return subprocess.CompletedProcess(arguments, 0, "", "")

    def absent_process(argv: tuple[str, ...], _timeout: float) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "process absent")

    monkeypatch.setattr(discovery, "probe_docker", absent_docker)
    monkeypatch.setattr(discovery, "_run_probe", absent_process)
    return system_temp
