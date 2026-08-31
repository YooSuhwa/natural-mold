"""Read-only CLI for validating PostgreSQL and E2E lifecycle evidence."""

from __future__ import annotations

import argparse
from pathlib import Path

from e2e_cleanup_checker import validate_live_absence as validate_e2e_live_absence
from e2e_cleanup_checker import validate_payload as validate_e2e_payload
from postgres_cleanup_checker import (
    load_manifest,
    validate_live_absence,
    validate_payload,
)


def _validate_manifest(manifest: Path) -> None:
    """Dispatch only after the shared no-follow manifest read boundary parsed the runner."""
    payload = load_manifest(manifest)
    if payload.get("runner") == "moldy-isolated-e2e" and payload.get("schema_version") == 1:
        validate_e2e_payload(payload)
        validate_e2e_live_absence(payload)
        return
    validate_payload(payload)
    validate_live_absence(payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifests", nargs="+", type=Path)
    args = parser.parse_args()
    for manifest in args.manifests:
        _validate_manifest(manifest)
    print(f"validated={len(args.manifests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
