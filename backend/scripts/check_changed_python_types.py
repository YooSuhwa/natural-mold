"""Run bounded Pyright diagnostics for Python files changed since a base commit."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Final, TypedDict

from changed_python_files import ChangedPythonError, collect_changed_python

TOOL_TIMEOUT_SECONDS: Final = 180


class _PyrightSummary(TypedDict):
    errorCount: int
    warningCount: int


class _PyrightOutput(TypedDict):
    summary: _PyrightSummary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    return parser


def _run_pyright(paths: tuple[Path, ...]) -> int:
    if not paths:
        print("changed-python-types selected=0 status=passed")
        return 0
    backend = Path(__file__).resolve().parent.parent
    executable = Path(sys.executable).with_name("pyright")
    try:
        result = subprocess.run(  # noqa: S603 - fixed venv executable and argv
            [
                str(executable),
                "--project",
                str(backend / "pyproject.toml"),
                "--outputjson",
                *map(str, paths),
            ],
            cwd=backend,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=TOOL_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        print(f"changed-python-types selected={len(paths)} status=tool-error")
        return 2
    try:
        output: _PyrightOutput = json.loads(result.stdout)
        errors = int(output["summary"]["errorCount"])
        warnings = int(output["summary"]["warningCount"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        print(f"changed-python-types selected={len(paths)} status=tool-error")
        return 2
    status = "passed" if result.returncode == 0 and errors == 0 else "failed"
    print(
        f"changed-python-types selected={len(paths)} errors={errors} "
        f"warnings={warnings} status={status}"
    )
    return 0 if status == "passed" else 1


def main() -> int:
    """Collect paths and run Pyright without reflecting untrusted values."""
    args = _parser().parse_args()
    try:
        paths = collect_changed_python(args.base)
    except ChangedPythonError:
        print("changed-python-types status=collector-error", file=sys.stderr)
        return 2
    return _run_pyright(paths)


if __name__ == "__main__":
    raise SystemExit(main())
