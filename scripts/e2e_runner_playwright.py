"""Parse Playwright JSON receipts into exact, stable test identities."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


@dataclass(frozen=True, slots=True, order=True)
class PlaywrightNode:
    project: str
    spec: str
    title: str

    @property
    def node_id(self) -> str:
        return f"{self.project}::{self.spec}::{self.title}"


class PlaywrightReceiptError(RuntimeError):
    """Stable parse failure for an untrusted Playwright receipt."""


MAX_PLAYWRIGHT_RECEIPT_BYTES: Final = 2 * 1024 * 1024
MAX_PLAYWRIGHT_SUITE_DEPTH: Final = 32
MAX_PLAYWRIGHT_SUITES: Final = 1024
MAX_PLAYWRIGHT_NODES: Final = 1024
MAX_PLAYWRIGHT_RESULTS: Final = 512
MAX_PLAYWRIGHT_STRING_LENGTH: Final = 4096
MAX_PLAYWRIGHT_TITLE_LENGTH: Final = 1024
MAX_PLAYWRIGHT_NODE_ID_LENGTH: Final = 2048
PLAYWRIGHT_EXPECTED_STATUSES: Final = frozenset(
    {"passed", "failed", "timedOut", "skipped", "interrupted"}
)
PLAYWRIGHT_RESULT_STATUSES: Final = PLAYWRIGHT_EXPECTED_STATUSES


CANONICAL_LIVE_CASES = (
    (
        "e2e/builder.spec.ts",
        "starts a session and runs the build pipeline from an initial message",
    ),
    ("e2e/operator-screens.spec.ts", "System LLM shows the seed-configured role slots"),
    (
        "e2e/operator-screens.spec.ts",
        "creates and deletes a system credential through the catalog modal",
    ),
    (
        "e2e/agent-triggers.spec.ts",
        "a created interval trigger renders in the settings triggers tab",
    ),
)


def _string(value: JsonValue | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PlaywrightReceiptError("invalid_playwright_shape")
    if len(value) > MAX_PLAYWRIGHT_STRING_LENGTH:
        raise PlaywrightReceiptError("playwright_string_too_long")
    return value


def _sequence(value: JsonValue | None) -> list[JsonValue]:
    if not isinstance(value, list):
        raise PlaywrightReceiptError("invalid_playwright_shape")
    return value


def _child_sequence(mapping: dict[str, JsonValue], key: str) -> list[JsonValue]:
    return _sequence(mapping.get(key, []))


def _leaf_title(spec: dict[str, JsonValue], test: dict[str, JsonValue]) -> str | None:
    raw = _string(test.get("title")) or _string(spec.get("title"))
    if raw is None:
        return None
    title = raw.strip()
    if len(title) > MAX_PLAYWRIGHT_TITLE_LENGTH:
        raise PlaywrightReceiptError("playwright_title_too_long")
    return title or None


def _project_name(test: dict[str, JsonValue], result: dict[str, JsonValue] | None) -> str | None:
    test_project = _string(test.get("projectName"))
    result_project = _string(result.get("projectName")) if result is not None else None
    if test_project and result_project and test_project != result_project:
        raise PlaywrightReceiptError("ambiguous_playwright_result")
    return test_project or result_project


def _spec_path(spec: dict[str, JsonValue], suite_file: str | None) -> str | None:
    raw = _string(spec.get("file")) or suite_file
    if not raw:
        return None
    normalized = PurePosixPath(raw.replace("\\", "/"))
    parts = normalized.parts
    if "e2e" in parts:
        return str(PurePosixPath(*parts[parts.index("e2e") :]))
    return str(PurePosixPath("e2e", normalized.name))


def _read_receipt(path: Path) -> str:
    """Read one stable, regular receipt without following a path link."""
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise PlaywrightReceiptError("unsafe_playwright_receipt_file")
        if before.st_size > MAX_PLAYWRIGHT_RECEIPT_BYTES:
            raise PlaywrightReceiptError("playwright_receipt_too_large")
        if not hasattr(os, "O_NOFOLLOW"):
            raise PlaywrightReceiptError("unsafe_playwright_receipt_file")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise PlaywrightReceiptError("unsafe_playwright_receipt_file") from error
    with os.fdopen(descriptor, "rb") as receipt:
        try:
            opened = os.fstat(receipt.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != before.st_dev
                or opened.st_ino != before.st_ino
            ):
                raise PlaywrightReceiptError("unsafe_playwright_receipt_file")
            data = receipt.read(MAX_PLAYWRIGHT_RECEIPT_BYTES + 1)
        except OSError as error:
            raise PlaywrightReceiptError("invalid_playwright_json") from error
    if len(data) > MAX_PLAYWRIGHT_RECEIPT_BYTES:
        raise PlaywrightReceiptError("playwright_receipt_too_large")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PlaywrightReceiptError("invalid_playwright_json") from error


def _walk_suites(suites: list[JsonValue]) -> set[PlaywrightNode]:
    nodes: set[PlaywrightNode] = set()
    stack = [(suite, 1) for suite in reversed(suites)]
    suite_count = node_count = result_count = 0
    while stack:
        raw_suite, depth = stack.pop()
        if depth > MAX_PLAYWRIGHT_SUITE_DEPTH:
            raise PlaywrightReceiptError("playwright_suite_depth_limit")
        if not isinstance(raw_suite, dict):
            raise PlaywrightReceiptError("invalid_playwright_shape")
        suite_count += 1
        if suite_count > MAX_PLAYWRIGHT_SUITES:
            raise PlaywrightReceiptError("playwright_suite_limit")
        suite_file = _string(raw_suite.get("file"))
        for raw_spec in _child_sequence(raw_suite, "specs"):
            if not isinstance(raw_spec, dict):
                raise PlaywrightReceiptError("invalid_playwright_shape")
            path = _spec_path(raw_spec, suite_file)
            for raw_test in _child_sequence(raw_spec, "tests"):
                if not isinstance(raw_test, dict):
                    raise PlaywrightReceiptError("invalid_playwright_shape")
                node_count += 1
                if node_count > MAX_PLAYWRIGHT_NODES:
                    raise PlaywrightReceiptError("playwright_node_limit")
                expected_status = _string(raw_test.get("expectedStatus"))
                if expected_status is None:
                    raise PlaywrightReceiptError("missing_playwright_status")
                if expected_status not in PLAYWRIGHT_EXPECTED_STATUSES:
                    raise PlaywrightReceiptError("invalid_playwright_status")
                results = _child_sequence(raw_test, "results")
                result_count += len(results)
                if result_count > MAX_PLAYWRIGHT_RESULTS:
                    raise PlaywrightReceiptError("playwright_result_limit")
                if len(results) > 1:
                    raise PlaywrightReceiptError("ambiguous_playwright_results")
                result: dict[str, JsonValue] | None = None
                if results:
                    candidate = results[0]
                    if not isinstance(candidate, dict):
                        raise PlaywrightReceiptError("invalid_playwright_shape")
                    result = candidate
                    result_status = _string(result.get("status"))
                    if result_status is None:
                        raise PlaywrightReceiptError("missing_playwright_status")
                    if result_status not in PLAYWRIGHT_RESULT_STATUSES:
                        raise PlaywrightReceiptError("invalid_playwright_status")
                else:
                    result_status = None
                if expected_status == "skipped" and result_status not in {
                    None,
                    "skipped",
                }:
                    raise PlaywrightReceiptError("contradictory_playwright_status")
                if expected_status == "skipped" or result_status == "skipped":
                    continue
                project = _project_name(raw_test, result)
                title = _leaf_title(raw_spec, raw_test)
                if project is None or path is None or title is None:
                    raise PlaywrightReceiptError("invalid_playwright_shape")
                node = PlaywrightNode(project, path, title)
                if len(node.node_id) > MAX_PLAYWRIGHT_NODE_ID_LENGTH:
                    raise PlaywrightReceiptError("playwright_node_id_too_long")
                if node in nodes:
                    raise PlaywrightReceiptError("duplicate_playwright_node")
                nodes.add(node)
        for child in reversed(_child_sequence(raw_suite, "suites")):
            stack.append((child, depth + 1))
    return nodes


def parse_playwright_json(path: Path) -> tuple[PlaywrightNode, ...]:
    try:
        decoded: JsonValue = json.loads(_read_receipt(path))
    except (
        UnicodeError,
        json.JSONDecodeError,
        MemoryError,
        RecursionError,
        ValueError,
    ) as error:
        raise PlaywrightReceiptError("invalid_playwright_json") from error
    if not isinstance(decoded, dict):
        raise PlaywrightReceiptError("invalid_playwright_shape")
    nodes = _walk_suites(_sequence(decoded.get("suites")))
    if not nodes:
        raise PlaywrightReceiptError("empty_playwright_selection")
    return tuple(sorted(nodes))


def parse_live_cases(raw: str, project: str) -> tuple[PlaywrightNode, ...]:
    try:
        raw_bytes = raw.encode("utf-8")
    except UnicodeEncodeError as error:
        raise PlaywrightReceiptError("invalid_live_cases") from error
    if len(raw_bytes) > MAX_PLAYWRIGHT_RECEIPT_BYTES:
        raise PlaywrightReceiptError("live_cases_too_large")
    try:
        decoded: JsonValue = json.loads(raw)
    except (json.JSONDecodeError, MemoryError, RecursionError, ValueError) as error:
        raise PlaywrightReceiptError("invalid_live_cases") from error
    cases = _sequence(decoded)
    nodes: set[PlaywrightNode] = set()
    for value in cases:
        if not isinstance(value, dict):
            raise PlaywrightReceiptError("invalid_live_case")
        spec = _string(value.get("spec"))
        title = _string(value.get("title"))
        if spec is None or title is None:
            raise PlaywrightReceiptError("invalid_live_case")
        if len(title) > MAX_PLAYWRIGHT_TITLE_LENGTH:
            raise PlaywrightReceiptError("playwright_title_too_long")
        node = PlaywrightNode(project, spec, title)
        if len(node.node_id) > MAX_PLAYWRIGHT_NODE_ID_LENGTH:
            raise PlaywrightReceiptError("playwright_node_id_too_long")
        if node in nodes:
            raise PlaywrightReceiptError("duplicate_playwright_node")
        nodes.add(node)
    if len(nodes) != 4:
        raise PlaywrightReceiptError("live_case_count")
    return tuple(sorted(nodes))


def canonical_live_nodes(project: str) -> tuple[PlaywrightNode, ...]:
    nodes = (PlaywrightNode(project, spec, title) for spec, title in CANONICAL_LIVE_CASES)
    return tuple(sorted(nodes))


def canonical_live_cases_json() -> str:
    cases = [{"spec": spec, "title": title} for spec, title in CANONICAL_LIVE_CASES]
    return json.dumps(cases, separators=(",", ":"))


def canonical_live_title_filter() -> str:
    escaped = [
        re.sub(r"[.*+?^${}()|[\]\\]", r"\\\g<0>", title) for _, title in CANONICAL_LIVE_CASES
    ]
    return f"(?:{'|'.join(escaped)})$"


def assert_exact_selection(
    selected: tuple[PlaywrightNode, ...], expected: tuple[PlaywrightNode, ...]
) -> None:
    if selected != expected:
        raise PlaywrightReceiptError("selection_mismatch")
