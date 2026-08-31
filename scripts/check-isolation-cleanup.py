"""Read-only CLI for validating PostgreSQL lifecycle evidence."""

from __future__ import annotations

import argparse
from pathlib import Path

from postgres_cleanup_checker import load_and_validate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifests", nargs="+", type=Path)
    args = parser.parse_args()
    for manifest in args.manifests:
        load_and_validate(manifest)
    print(f"validated={len(args.manifests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
