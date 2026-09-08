"""Executable contract tests for the repository pre-push hook."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
PRE_PUSH_HOOK: Final = REPO_ROOT / ".husky" / "pre-push"


def test_pre_push_hook_isolates_nested_git_and_installs_locked_dev_tools(
    tmp_path: Path,
) -> None:
    # Given Git-local variables from a linked-worktree push and harmless tool stubs.
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir()
    private_tmp = tmp_path / "private-tmp"
    private_tmp.mkdir()
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    for command in ("uv", "pnpm"):
        stub = stub_bin / command
        stub.write_text(
            "#!/bin/sh\n"
            f'printf \'%s\\n\' "${{GIT_DIR-unset}}" "${{GIT_WORK_TREE-unset}}" '
            f'> "$CAPTURE_DIR/{command}-env"\n'
            f'printf \'%s\\n\' "$@" > "$CAPTURE_DIR/{command}-args"\n'
        )
        stub.chmod(0o755)

    environment = {
        **os.environ,
        "CAPTURE_DIR": str(capture_dir),
        "GIT_DIR": str(REPO_ROOT / ".git"),
        "GIT_WORK_TREE": str(REPO_ROOT),
        "PATH": f"{stub_bin}{os.pathsep}{os.environ['PATH']}",
        "TMPDIR": str(private_tmp),
    }

    # When the real hook runs through its normal shell entry point.
    result = subprocess.run(
        ["/bin/sh", str(PRE_PUSH_HOOK)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    # Then child tools receive no repository-local Git state and uv supplies locked dev tools.
    assert result.returncode == 0, result.stderr
    assert (capture_dir / "uv-env").read_text().splitlines() == ["unset", "unset"]
    assert (capture_dir / "pnpm-env").read_text().splitlines() == ["unset", "unset"]
    uv_arguments = (capture_dir / "uv-args").read_text().splitlines()
    pytest_basetemp_argument = uv_arguments[-1]
    assert pytest_basetemp_argument.startswith("--basetemp=")
    assert uv_arguments[:-1] == [
        "run",
        "--locked",
        "--extra",
        "dev",
        "pytest",
        "tests/",
        "-q",
        "--tb=line",
    ]
    pytest_basetemp = pytest_basetemp_argument.removeprefix("--basetemp=")
    assert pytest_basetemp.startswith(f"{private_tmp}/moldy-pre-push-pytest.")
    assert not Path(pytest_basetemp).exists()
