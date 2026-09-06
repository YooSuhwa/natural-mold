"""Fail when backend runtime dependency debt changes without review."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

if __package__:
    from .runtime_dependency_graph import DependencyGraphError, scan_repository
    from .runtime_dependency_policy import DependencyPolicyError, check, load_baseline
else:
    from runtime_dependency_graph import DependencyGraphError, scan_repository
    from runtime_dependency_policy import DependencyPolicyError, check, load_baseline

DEFAULT_BACKEND_ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE: Final = Path(__file__).with_name("runtime-dependency-baseline.json")
USAGE: Final = "usage: check_runtime_dependencies.py [--backend-root PATH] [--baseline PATH]"


@dataclass(frozen=True, slots=True)
class CliOptions:
    backend_root: Path
    baseline: Path


@dataclass(frozen=True, slots=True)
class CliArgumentError(Exception):
    reason: str

    def __str__(self) -> str:
        return self.reason


def parse_options(arguments: Sequence[str]) -> CliOptions:
    """Parse the deliberately small read-only CLI surface."""
    backend_root = DEFAULT_BACKEND_ROOT
    baseline = DEFAULT_BASELINE
    index = 0
    while index < len(arguments):
        option = arguments[index]
        if option in {"--backend-root", "--baseline"}:
            if index + 1 >= len(arguments):
                raise CliArgumentError(f"missing value for {option}")
            value = Path(arguments[index + 1])
            if option == "--backend-root":
                backend_root = value
            else:
                baseline = value
            index += 2
            continue
        raise CliArgumentError(f"unsupported option: {option}")
    return CliOptions(backend_root, baseline)


def run(options: CliOptions) -> tuple[int, tuple[str, ...]]:
    """Execute the read-only guard and return its observable result."""
    graph = scan_repository(options.backend_root)
    baseline = load_baseline(options.baseline)
    result = check(graph, baseline)
    if result.is_clean:
        return 0, (
            "runtime dependency guard passed: "
            f"{len(result.current.violations)} reviewed violations, "
            f"{len(result.current.cyclic_edges)} reviewed cyclic edges",
        )
    return 1, result.diagnostics


def main(arguments: Sequence[str] | None = None) -> int:
    """CLI boundary with concise, secret-free diagnostics."""
    try:
        options = parse_options(sys.argv[1:] if arguments is None else arguments)
        exit_code, messages = run(options)
    except (CliArgumentError, DependencyGraphError, DependencyPolicyError) as error:
        print(f"runtime dependency guard failed: {error}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2
    stream = sys.stdout if exit_code == 0 else sys.stderr
    for message in messages:
        print(message, file=stream)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
