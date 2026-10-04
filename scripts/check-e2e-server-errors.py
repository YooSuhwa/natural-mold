"""Fail Nightly on database errors in its contract-validated redacted log."""

from __future__ import annotations

import sys
from pathlib import Path

from e2e_server_error_checker import database_lifecycle_errors


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check-e2e-server-errors.py <validated-execution.stderr.log>", file=sys.stderr)
        return 64
    try:
        stderr = Path(sys.argv[1]).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        print("e2e_server_log_unreadable", file=sys.stderr)
        return 2
    errors = database_lifecycle_errors(stderr)
    if errors:
        # Report stable codes only; raw logs can contain credential or user data.
        print("e2e_server_errors=" + ",".join(sorted(errors)), file=sys.stderr)
        return 1
    print("e2e_server_errors=none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
