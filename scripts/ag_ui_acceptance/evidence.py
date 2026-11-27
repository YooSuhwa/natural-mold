"""Extract browser gate results and scoped diagnostics from an isolated stack."""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt
from pydantic.alias_generators import to_camel


def collect_failure(
    logs: Path,
    record: dict,
    exc_type: type[BaseException] | None,
    _exc: BaseException | None,
    _trace: TracebackType | None,
) -> bool:
    if exc_type is not None:
        failures = {}
        for p in sorted(logs.glob("*.log")):
            lines = p.read_text(errors="replace").splitlines()
            frames = []
            errors = []
            for line in lines:
                loc = re.search(r'File "([^"]+\.py)", line (\d+), in ([\w<.>-]+)', line)
                if loc:
                    frames.append(f"{Path(loc[1]).name}:{loc[2]}:{loc[3]}")
                cls = re.search(r"^([\w.]+(?:Error|Exception))(?:\s*:\s*(.*))?", line)
                if cls:
                    errors.append(cls[1])
                    # PostgreSQL's relation and constraint names identify a broken migration/
                    # scope. Leave SQL parameters and value-bearing exception text in temp logs.
                    name = re.search(
                        r'(?:table|relation|constraint|column) "([a-z_][a-z0-9_]{0,63})"',
                        cls[2] or "",
                    )
                    if name:
                        errors.append(f"schema:{name[1]}")
            if frames or errors:
                failures[p.stem] = {
                    "frames": frames[-35:],
                    "classes": list(dict.fromkeys(errors))[-15:],
                }
        record["failure_diagnostics"] = failures
    return False


STEER_CUTOVER_TITLE = (
    "preserves a paused goal, pending steer and queued input through an in-flight transport change"
)
CUTOVER_CASES = {
    "reads one delegated chat across replicas, approval, AG-UI cutover and rollback": "cutover",
    STEER_CUTOVER_TITLE: "steer-and-goal-cutover",
}
PERF_TITLE = "streams progressively within sane time bounds (no buffering bottleneck)"
OUTCOMES = ("expected", "unexpected", "flaky", "skipped")
STATUSES = ("passed", "failed", "timedOut", "skipped", "interrupted")
MAX_ATTACHMENT_BYTES = 65536


def _opaque_id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        # Protocol-visible IDs can be opaque. Retain equality without storing arbitrary text.
        return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


PublicId = Annotated[str, Field(min_length=1, max_length=1024), AfterValidator(_opaque_id)]


class _PhysicalRun(BaseModel):
    id: PublicId
    parent_run_id: PublicId | None
    status: Literal[
        "queued", "running", "canceling", "completed", "failed", "interrupted", "canceled", "stale"
    ]


class _BrowserEvidence(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)


class _Artifact(_BrowserEvidence):
    id: PublicId
    display_name: Literal["moldy-langgraph-v3-report.md", "moldy-langgraph-v3-notes.txt"]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class _Cutover(_BrowserEvidence):
    conversation_id: PublicId
    historical_run: PublicId
    public_runs: Annotated[list[PublicId], Field(min_length=3, max_length=3)]
    persisted_runs: Annotated[list[_PhysicalRun], Field(min_length=1)]
    old_message_id: PublicId
    pinned_checkpoint: PublicId
    feedback_id: PublicId
    artifacts: Annotated[list[_Artifact], Field(min_length=2, max_length=2)]
    writes: tuple[Literal["run.start"], Literal["ag-ui"], Literal["ag-ui"]]
    reader_writes: tuple[Literal["ag-ui"]]


class _SteerAndGoal(_BrowserEvidence):
    conversation_id: PublicId
    run_id: PublicId
    goal_id: PublicId
    goal_revision: Annotated[StrictInt, Field(ge=1)]
    steer_id: PublicId
    moved_input_id: PublicId
    next_input_id: PublicId
    physical_runs: Annotated[list[_PhysicalRun], Field(min_length=1)]


def cutover_attachments(results: list[dict], owned_root: Path) -> dict:
    captured = {}
    schemas = {"cutover": _Cutover, "steer-and-goal-cutover": _SteerAndGoal}
    for result in results:
        for att in result.get("attachments", []):
            name = att.get("name")
            if name not in schemas:
                continue
            if name in captured or att.get("contentType") != "application/json":
                raise ValueError("Duplicate or invalid cutover attachment")
            if "body" in att:
                encoded = att["body"]
                if not isinstance(encoded, str) or len(encoded) > 4 * (
                    (MAX_ATTACHMENT_BYTES + 2) // 3
                ):
                    raise ValueError("Invalid cutover attachment size")
                body = base64.b64decode(encoded, validate=True)
            else:
                path = (owned_root / att["path"]).resolve()
                if not path.is_relative_to(owned_root.resolve()) or not path.is_file():
                    raise ValueError("Unexpected cutover attachment path")
                with path.open("rb") as stream:
                    body = stream.read(MAX_ATTACHMENT_BYTES + 1)
            if len(body) > MAX_ATTACHMENT_BYTES:
                raise ValueError("Invalid cutover attachment size")
            # Export only the known identifier, status and digest fields. Extra input is dropped.
            captured[name] = (
                schemas[name].model_validate_json(body).model_dump(mode="json", by_alias=True)
            )
    return captured


def _spec_file(value: str) -> str:
    return value.removeprefix("frontend/").removeprefix("e2e/")


def _specs(suite: dict) -> Iterator[dict]:
    yield from suite.get("specs", [])
    for child in suite.get("suites", []):
        yield from _specs(child)


def _error_source(result: dict, spec: str) -> dict:
    # Assertion messages can contain raw expected/received text, selectors and credentials.
    error = result.get("error") or {}
    for location in (error.get("location"), result.get("errorLocation")):
        if not isinstance(location, dict):
            continue
        filename = location.get("file", "")
        if not (filename == spec or filename.endswith("/e2e/" + spec)):
            continue
        if all(type(location.get(key)) is int and location[key] > 0 for key in ("line", "column")):
            return {"file": spec, "line": location["line"], "column": location["column"]}
    return {}


def _assertion_shape(error: dict) -> dict:
    # Playwright's result.error contains a colorized message, snippet and stack. Preserve the
    # matcher and operand types while keeping actual values, browser text and URLs private.
    message = re.sub(r"\x1b\[[0-9;]*m", "", (error.get("message") or "")[:16000])
    matcher = re.search(r"expect\([^\n]{0,90}\)\.(?:not\.)?(to[A-Z][A-Za-z]+)\(", message)
    names = {
        "toBe",
        "toEqual",
        "toStrictEqual",
        "toMatchObject",
        "toContain",
        "toContainEqual",
        "toMatch",
        "toHaveLength",
        "toHaveCount",
        "toBeVisible",
        "toBeHidden",
        "toBeEnabled",
        "toBeDisabled",
        "toContainText",
        "toHaveText",
        "toHaveAttribute",
        "toBeTruthy",
        "toBeFalsy",
        "toBeNull",
        "toBeUndefined",
        "toBeGreaterThan",
        "toBeLessThan",
        "toBeGreaterThanOrEqual",
        "toBeLessThanOrEqual",
    }
    if not matcher or matcher[1] not in names:
        return {}
    diagnostic = {"matcher": matcher[1]}
    for operand in ("Expected", "Received"):
        found = re.search(rf"^{operand}:\s*([^\n]+)$", message, re.MULTILINE)
        if found:
            value = found[1].strip()
            shape = "other"
            if re.fullmatch(r"-?\d+(?:\.\d+)?", value):
                shape = "number"
            elif value.startswith('"'):
                shape = "string"
            elif value in {"true", "false"}:
                shape = "boolean"
            elif value == "null":
                shape = "null"
            diagnostic[operand.lower() + "_type"] = shape
    return diagnostic


def _perf_metrics(result: dict) -> dict | None:
    annotations = [
        a.get("description") for a in result.get("annotations", []) if a.get("type") == "perf"
    ]
    if len(annotations) != 1 or not isinstance(annotations[0], str):
        return None
    match = re.fullmatch(r"TTFT=(\d{1,9})ms total=(\d{1,9})ms spread=(\d{1,9})ms", annotations[0])
    if not match:
        return None
    return dict(zip(("ttft_ms", "total_ms", "spread_ms"), map(int, match.groups()), strict=True))


def _within_perf_bounds(metrics: dict) -> bool:
    ttft, total, spread = (metrics[key] for key in ("ttft_ms", "total_ms", "spread_ms"))
    # The spec asserts strict bounds on unrounded times. Math.round can place a passing
    # sample exactly on a bound and round(total - ttft) can differ by a millisecond.
    return ttft <= 20_000 and total <= 40_000 and spread >= 1500 and abs(total - ttft - spread) <= 1


def _case(spec: dict, test: dict, owned_root: Path, group: str) -> dict:
    filename = _spec_file(spec["file"])
    title = spec["title"]
    expected, outcome = test.get("expectedStatus"), test.get("status")
    results = test.get("results", [])
    errors = []
    attempts = []
    observed = {}
    for position, result in enumerate(results):
        status = result.get("status")
        attempt = {
            "status": status if status in STATUSES else "unknown",
            "duration_ms": result.get("duration"),
            "retry": result.get("retry"),
            "error_count": max(len(result.get("errors", [])), int(bool(result.get("error")))),
        }
        if attempt["error_count"]:
            attempt["error_source"] = _error_source(result, filename)
            attempt["assertion"] = _assertion_shape(result.get("error") or {})
        if status == "skipped":
            types = {
                a.get("type")
                for a in (*test.get("annotations", []), *result.get("annotations", []))
            }
            attempt["skip_kind"] = (
                "fixme" if "fixme" in types else "skip" if "skip" in types else "unexecuted"
            )
        if group.startswith("performance-"):
            attempt["perf"] = _perf_metrics(result)
            if not attempt["perf"] or not _within_perf_bounds(attempt["perf"]):
                errors.append("missing_or_out_of_bounds_performance_sample")
        if group == "cutover" and title in CUTOVER_CASES:
            try:
                observed = cutover_attachments([result], owned_root)
            except (KeyError, OSError, ValueError, TypeError):
                errors.append("invalid_cutover_attachment")
            if set(observed) != {CUTOVER_CASES[title]}:
                errors.append("missing_or_misplaced_cutover_attachment")
        if (
            attempt["retry"] != position
            or type(attempt["duration_ms"]) is not int
            or attempt["duration_ms"] < 0
        ):
            errors.append("invalid_attempt")
        attempts.append(attempt)
    if not (
        len(attempts) == 1
        and expected == "passed"
        and outcome == "expected"
        and attempts[0]["status"] == "passed"
        and not attempts[0]["error_count"]
    ):
        errors.append("requires_one_first_attempt_pass")
    return {
        "file": filename,
        "title": title,
        "expected_status": expected if expected in STATUSES else "unknown",
        "outcome": outcome if outcome in OUTCOMES else "unknown",
        "attempts": attempts,
        "observations": observed,
        "errors": sorted(set(errors)),
        "passed": not errors,
    }


def playwright_report(data: dict, owned_root: Path, *, group: str, specs: list[str]) -> dict:
    """Check a pinned Playwright report for single-attempt, verified passes."""
    requested = {_spec_file(path) for path in specs}
    cases = [
        _case(spec, test, owned_root, group)
        for suite in data.get("suites", [])
        for spec in _specs(suite)
        for test in spec.get("tests", [])
    ]
    errors = []
    counts = {outcome: sum(c["outcome"] == outcome for c in cases) for outcome in OUTCOMES}
    if not cases or {case["file"] for case in cases} != requested:
        errors.append("missing_or_unexpected_spec")
    if data.get("errors") != []:
        errors.append("runner_error")
    stats = data.get("stats", {})
    if any(stats.get(outcome) != count for outcome, count in counts.items()):
        errors.append("report_totals_mismatch")
    if group == "cutover" and {c["title"] for c in cases} != set(CUTOVER_CASES):
        errors.append("missing_or_unexpected_cutover_case")
    if group.startswith("performance-") and [c["title"] for c in cases] != [PERF_TITLE]:
        errors.append("missing_or_unexpected_performance_case")
    if len({(case["file"], case["title"]) for case in cases}) != len(cases):
        errors.append("duplicate_case")
    if any(not case["passed"] for case in cases):
        errors.append("unverified_case")
    counts["verified"] = sum(case["passed"] for case in cases)
    counts["total"] = len(cases)
    return {"cases": cases, "counts": counts, "errors": errors, "passed": not errors}
