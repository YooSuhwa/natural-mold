from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from postgres_runner_runtime import run_test_child  # noqa: E402


def _isolated_script(tmp_path: Path) -> Path:
    script_dir = tmp_path / "scripts"
    python_dir = tmp_path / "backend" / ".venv" / "bin"
    script_dir.mkdir(parents=True)
    python_dir.mkdir(parents=True)
    script = script_dir / "run-isolated-postgres-tests.sh"
    shutil.copy2(REPO_ROOT / "scripts" / script.name, script)
    fake_python = python_dir / "python"
    fake_python.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
    fake_python.chmod(0o700)
    return script


def test_shell_translates_only_ordered_composite_selector(tmp_path: Path) -> None:
    # Given the public shell runner with an isolated fake Python boundary.
    script = _isolated_script(tmp_path)

    # When the exact ordered selector is invoked.
    result = subprocess.run(
        [
            "/bin/bash",
            str(script),
            "run-lifecycle",
            "stream-resume",
            "--manifest",
            "receipt.json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    # Then it translates to one bounded internal scenario.
    assert result.returncode == 0
    assert result.stdout.splitlines()[-3:] == [
        "run-lifecycle+stream-resume",
        "--manifest",
        "receipt.json",
    ]


def test_shell_rejects_reverse_duplicate_and_extra_composite_selectors(tmp_path: Path) -> None:
    # Given the public shell runner.
    script = _isolated_script(tmp_path)

    # When unsupported selector variants cross its boundary.
    invocations = [
        ["stream-resume", "run-lifecycle", "--manifest", "x.json"],
        ["run-lifecycle", "run-lifecycle", "--manifest", "x.json"],
        ["run-lifecycle", "stream-resume", "extra", "--manifest", "x.json"],
    ]
    results = [
        subprocess.run(
            ["/bin/bash", str(script), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
        for arguments in invocations
    ]

    # Then every variant fails with EX_USAGE before starting Python.
    assert [result.returncode for result in results] == [64, 64, 64]


def test_composite_child_selects_exactly_two_integration_files(
    monkeypatch,
) -> None:
    # Given a disposable-lane environment and a command capture seam.
    commands: list[list[str]] = []

    def record_command(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("postgres_runner_runtime.run_command", record_command)
    monkeypatch.setattr(
        "postgres_runner_runtime.parse_lane_dsns",
        lambda _dsns: type("Target", (), {"password": "dummy"})(),
    )
    environment = {
        "DATABASE_URL": "postgresql+asyncpg://runner:dummy@127.0.0.1:49152/moldy_pg_lane_x",
        "DATABASE_URL_SYNC": "postgresql://runner:dummy@127.0.0.1:49152/moldy_pg_lane_x",
        "INTEGRATION_DATABASE_URL": (
            "postgresql+psycopg://runner:dummy@127.0.0.1:49152/moldy_pg_lane_x"
        ),
    }

    # When the internal composite scenario starts its only child process.
    exit_code, _ = run_test_child(environment, "run-lifecycle+stream-resume")

    # Then one pytest invocation contains exactly the two approved files.
    assert exit_code == 0
    assert commands[0][4:6] == [
        "tests/integration/test_conversation_run_lifecycle.py",
        "tests/integration/test_stream_resume.py",
    ]
    assert len(commands) == 1
