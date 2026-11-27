"""Resolve acceptance cases against the tests Playwright actually collects."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

from processes import command

_SPEC = re.compile(r"e2e/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.spec\.ts")


def resolve_case_specs(
    frontend: Path,
    env: dict[str, str],
    logs: Path,
    probes: Path,
    case_selectors: Sequence[str],
    explicit_specs: Sequence[str],
) -> list[str]:
    """Resolve file::exact-title selectors, then validate explicit file[:line] selections."""
    requests: list[tuple[str, str | int | None]] = []
    for selector in case_selectors:
        file, delimiter, title = selector.partition("::")
        if not delimiter or not title.strip() or len(title.splitlines()) != 1:
            raise ValueError(f"Expected a spec path and exact test title: {selector}")
        requests.append((file, title))
    for selector in explicit_specs:
        file, delimiter, line = selector.partition(":")
        if delimiter and not re.fullmatch(r"[1-9][0-9]*", line):
            raise ValueError(f"Invalid explicit spec line: {selector}")
        requests.append((file, int(line) if delimiter else None))
    if not requests:
        return []
    files = sorted({file for file, _ in requests})
    for file in files:
        path = frontend / file
        if (
            not _SPEC.fullmatch(file)
            or not path.is_file()
            or not path.resolve().is_relative_to((frontend / "e2e").resolve())
        ):
            raise ValueError(f"Missing or invalid acceptance spec path: {file}")

    with TemporaryDirectory(prefix="case-list-", dir=logs) as directory:
        report = Path(directory).resolve() / "catalog.json"
        command(
            "playwright-list",
            [
                "pnpm",
                "exec",
                "playwright",
                "test",
                "--list",
                "--reporter=json",
                "-c",
                str(probes / "playwright.cutover.cjs"),
                *files,
            ],
            frontend,
            {**env, "PLAYWRIGHT_JSON_OUTPUT_FILE": str(report)},
            logs,
            90,
        )
        data = json.loads(report.read_bytes())
    if (
        data.get("errors")
        or Path(data["config"]["rootDir"]).resolve() != (frontend / "e2e").resolve()
    ):
        raise RuntimeError("Playwright could not catalog the staged e2e directory")
    catalog: dict[str, list[tuple[str, int]]] = {file: [] for file in files}
    suites = list(data["suites"])
    while suites:
        suite = suites.pop()
        suites.extend(suite.get("suites", []))
        for spec in suite.get("specs", []):
            file = f"e2e/{spec['file']}"
            if file in catalog:
                catalog[file].append((spec["title"], spec["line"]))

    selections = []
    for file, target in requests:
        cases = catalog[file]
        if not cases:
            raise ValueError(f"Playwright collected no tests for {file}")
        if target is None:
            selections.append(file)
            continue
        if isinstance(target, str):
            lines = [line for title, line in cases if title == target]
            if len(lines) != 1:
                raise ValueError(f"{file}::{target} matched {len(lines)} tests")
            target = lines[0]
        count = sum(line == target for _, line in cases)
        if count != 1:
            raise ValueError(f"{file}:{target} matched {count} test declarations")
        selections.append(f"{file}:{target}")
    return list(dict.fromkeys(selections))
