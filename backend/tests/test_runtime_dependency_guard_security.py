"""Fail-closed input and CLI contracts for the Todo09 dependency guard."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_dependency_graph import (  # noqa: E402
    MAX_SOURCE_BYTES,
    DependencyGraphError,
    scan_repository,
)
from runtime_dependency_policy import DependencyPolicyError, load_baseline  # noqa: E402


def _baseline_data() -> dict[str, object]:
    return {
        "schema_version": 1,
        "reviewed_from": "0" * 40,
        "historical_base": "1" * 40,
        "legacy_violations": [],
        "legacy_cyclic_edges": [],
        "dynamic_import_exceptions": [],
    }


def _write_baseline(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("{", "not valid JSON"),
        ('{"schema_version": 1, "schema_version": 1}', "duplicate key"),
        (json.dumps({**_baseline_data(), "surprise": True}, indent=2) + "\n", "strict schema"),
        (
            json.dumps(
                {
                    **_baseline_data(),
                    "legacy_violations": [
                        {
                            "rule": "unknown",
                            "importer": "app/routers/a.py",
                            "imported": "app.models.a",
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            "strict schema",
        ),
    ],
)
def test_baseline_rejects_malformed_duplicate_and_unknown_schema(
    tmp_path: Path, payload: str, message: str
) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(DependencyPolicyError, match=message):
        load_baseline(path)


@pytest.mark.parametrize(
    "record",
    [
        {
            "rule": "routers_to_models",
            "importer": "app/routers/*.py",
            "imported": "app.models.user",
        },
        {
            "rule": "routers_to_models",
            "importer": "../app/routers/a.py",
            "imported": "app.models.user",
        },
        {
            "rule": "routers_to_models",
            "importer": "app\\routers\\a.py",
            "imported": "app.models.user",
        },
    ],
)
def test_baseline_rejects_unsafe_or_wildcard_legacy_exception(
    tmp_path: Path, record: dict[str, str]
) -> None:
    path = tmp_path / "baseline.json"
    _write_baseline(path, {**_baseline_data(), "legacy_violations": [record]})

    with pytest.raises(DependencyPolicyError, match="unsafe"):
        load_baseline(path)


def test_baseline_rejects_duplicate_or_noncanonical_records(tmp_path: Path) -> None:
    record = {
        "rule": "routers_to_models",
        "importer": "app/routers/a.py",
        "imported": "app.models.user",
    }
    path = tmp_path / "baseline.json"
    _write_baseline(path, {**_baseline_data(), "legacy_violations": [record, record]})

    with pytest.raises(DependencyPolicyError, match="sorted and unique"):
        load_baseline(path)


def test_baseline_rejects_noncanonical_json_layout(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(_baseline_data()) + "\n", encoding="utf-8")

    with pytest.raises(DependencyPolicyError, match="not canonical"):
        load_baseline(path)


@pytest.mark.parametrize("function", ["<module>", "outer.inner"])
def test_baseline_accepts_safe_dynamic_function_scopes(tmp_path: Path, function: str) -> None:
    path = tmp_path / "baseline.json"
    data = {
        **_baseline_data(),
        "dynamic_import_exceptions": [
            {
                "module": "app.agent_runtime.dynamic",
                "function": function,
                "callee": "importlib.import_module",
            }
        ],
    }
    _write_baseline(path, data)

    assert load_baseline(path).dynamic_import_exceptions[0].function == function


@pytest.mark.parametrize(
    "function",
    ["", ".inner", "outer.", "outer..inner", "../outer", "outer/inner", "outer\\inner", "*"],
)
def test_baseline_rejects_unsafe_dynamic_function_scopes(tmp_path: Path, function: str) -> None:
    path = tmp_path / "baseline.json"
    data = {
        **_baseline_data(),
        "dynamic_import_exceptions": [
            {
                "module": "app.agent_runtime.dynamic",
                "function": function,
                "callee": "importlib.import_module",
            }
        ],
    }
    _write_baseline(path, data)

    with pytest.raises(DependencyPolicyError, match="dynamic exception is unsafe"):
        load_baseline(path)


def _tracked_repo(tmp_path: Path, files: dict[str, bytes | str]) -> Path:
    root = tmp_path / "backend"
    for relative, payload in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_bytes(payload)
    executable = shutil.which("git")
    assert executable is not None
    subprocess.run([executable, "init", "-q"], cwd=root, check=True)
    subprocess.run([executable, "add", "app"], cwd=root, check=True)
    return root


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"\xff", "UTF-8"),
        ("def broken(:\n", "cannot be parsed"),
        (b"x" * (MAX_SOURCE_BYTES + 1), "exceeds size limit"),
    ],
    ids=("invalid_utf8", "syntax_error", "oversized"),
)
def test_scanner_fails_closed_for_invalid_tracked_sources(
    tmp_path: Path, payload: bytes | str, message: str
) -> None:
    root = _tracked_repo(tmp_path, {"app/routers/source.py": payload})

    with pytest.raises(DependencyGraphError, match=message):
        scan_repository(root)


def test_scanner_rejects_tracked_symlink(tmp_path: Path) -> None:
    root = _tracked_repo(tmp_path, {"app/routers/normal.py": "pass\n"})
    outside = tmp_path / "outside.py"
    outside.write_text("pass\n", encoding="utf-8")
    (root / "app/routers/link.py").symlink_to(outside)
    executable = shutil.which("git")
    assert executable is not None
    subprocess.run([executable, "add", "app/routers/link.py"], cwd=root, check=True)

    with pytest.raises(DependencyGraphError, match="symbolic link"):
        scan_repository(root)


def test_cli_rejects_write_and_update_options_without_mutating_baseline() -> None:
    backend = Path(__file__).resolve().parents[1]
    script = backend / "scripts" / "check_runtime_dependencies.py"
    baseline = backend / "scripts" / "runtime-dependency-baseline.json"
    before = baseline.read_bytes()

    for option in ("--write", "--update", "--write-baseline"):
        result = subprocess.run(
            [sys.executable, str(script), option],
            cwd=backend,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2
        assert "unsupported option" in result.stderr
        assert baseline.read_bytes() == before
