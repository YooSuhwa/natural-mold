"""Run Ruff's non-writing format check for Python files changed since a base."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Final

from changed_python_files import ChangedPythonError, collect_changed_python

TOOL_TIMEOUT_SECONDS: Final = 180


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    return parser


def _run_ruff(paths: tuple[Path, ...]) -> int:
    if not paths:
        print("changed-python-format selected=0 status=passed")
        return 0
    backend = Path(__file__).resolve().parent.parent
    executable = Path(sys.executable).with_name("ruff")
    try:
        result = subprocess.run(  # noqa: S603 - fixed venv executable and argv
            [
                str(executable),
                "format",
                "--check",
                "--config",
                str(backend / "pyproject.toml"),
                "--",
                *map(str, paths),
            ],
            cwd=backend,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=TOOL_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        print(f"changed-python-format selected={len(paths)} status=tool-error")
        return 2
    status = "passed" if result.returncode == 0 else "failed"
    print(f"changed-python-format selected={len(paths)} status={status}")
    return 0 if status == "passed" else 1


def main() -> int:
    """Collect paths and run Ruff without reflecting untrusted values."""
    args = _parser().parse_args()
    try:
        paths = collect_changed_python(args.base)
    except ChangedPythonError:
        print("changed-python-format status=collector-error", file=sys.stderr)
        return 2
    return _run_ruff(paths)


if __name__ == "__main__":
    raise SystemExit(main())
