"""Extract browser gate results and scoped diagnostics from an isolated stack."""

from __future__ import annotations

import base64
import hashlib
import json
import math
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


def _attachment_body(att: dict, owned_root: Path, role: str) -> bytes:
    if "body" in att:
        encoded = att["body"]
        if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_ATTACHMENT_BYTES + 2) // 3):
            raise ValueError("Invalid attachment size")
        body = base64.b64decode(encoded, validate=True)
    else:
        path = (owned_root / att["path"]).resolve()
        if not path.is_relative_to(owned_root.resolve()) or not path.is_file():
            raise ValueError(f"Unexpected {role} attachment path")
        with path.open("rb") as stream:
            body = stream.read(MAX_ATTACHMENT_BYTES + 1)
    if len(body) > MAX_ATTACHMENT_BYTES:
        raise ValueError("Invalid attachment size")
    return body


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
            body = _attachment_body(att, owned_root, "cutover")
            # Export only the known identifier, status and digest fields. Extra input is dropped.
            captured[name] = (
                schemas[name].model_validate_json(body).model_dump(mode="json", by_alias=True)
            )
    return captured


def _spec_file(value: str) -> str:
    return value.removeprefix("frontend/").removeprefix("e2e/")


def _spec_selector(value: str) -> tuple[str, int | None]:
    file, delimiter, suffix = _spec_file(value).rpartition(":")
    if delimiter and re.fullmatch(r"[1-9][0-9]*", suffix):
        return file, int(suffix)
    return _spec_file(value), None


def _specs(
    suite: dict, parents: tuple[tuple[str, int], ...] = ()
) -> Iterator[tuple[dict, tuple[int, ...]]]:
    file = _spec_file(suite.get("file", ""))
    line = suite.get("line")
    if type(line) is int and line > 0:
        parents = (*parents, (file, line))
    for spec in suite.get("specs", []):
        filename = _spec_file(spec["file"])
        declared = tuple(value for name, value in parents if name == filename)
        own = spec.get("line")
        if type(own) is int and own > 0:
            declared = (*declared, own)
        yield spec, declared
    for child in suite.get("suites", []):
        yield from _specs(child, parents)


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


# Route labels are fixed literals. Playwright annotations can contain IDs and private URLs.
_USAGE_ROUTES = {
    r"/api/conversations/[^/?#]+/ag-ui/(snapshot|stream|runs|events)": "ag_ui",
    (
        r"/api/conversations/[^/?#]+/(messages|run-inputs|run-message-links|runs|files|"
        r"artifacts|pinned-summary|followup-suggestion)"
    ): "conversation",
    r"/api/conversations/[^/?#]+/runs/[^/?#]+/(stream|events)": "run",
    r"/api/conversations/[^/?#]+/langgraph/threads/[^/?#]+/(commands|state|history)": "langgraph",
    r"/api/(agents/summary|auth/me|conversations/page|artifacts)": "api",
}
_USAGE_NETWORK_CODES = {
    "api_request_abort",
    "api_request_failure",
    "api_response_failure",
    "next_chunk_abort_current_document",
    "next_chunk_abort_prior_document",
    "next_chunk_abort_unobserved",
    "other_request_abort",
    "other_request_failure",
    "other_response_failure",
}


def _usage_network_annotations(annotations: list[dict]) -> dict:
    codes: set[str] = set()
    failures = []
    for item in annotations:
        kind, value = item.get("type"), item.get("description")
        if not isinstance(value, str):
            continue
        if kind == "moldy.network-failure.v1" and value in _USAGE_NETWORK_CODES:
            codes.add(value)
        if kind not in {"moldy.api-request-failure.v1", "moldy.response-failure.v1"}:
            continue
        try:
            data = json.loads(value)
        except ValueError:
            continue
        if not isinstance(data, dict) or not isinstance(path := data.get("pathname"), str):
            continue
        route = "other"
        for pattern, prefix in _USAGE_ROUTES.items():
            if match := re.fullmatch(pattern, path):
                route = prefix + "." + match[1]
                break
        method = data.get("method")
        failure = {
            "route": route,
            "method": method
            if method in ("GET", "HEAD", "POST", "PATCH", "DELETE", "PUT")
            else "other",
        }
        if kind == "moldy.response-failure.v1":
            status = data.get("status")
            if type(status) is not int or not 400 <= status <= 599:
                continue
            failure["status"] = status
        else:
            failure["transport"] = (
                "aborted" if data.get("errorText") == "net::ERR_ABORTED" else "failed"
            )
        if failure not in failures:
            failures.append(failure)
    return {"codes": sorted(codes), "failures": failures}


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


_PHASES = ("sourceEpochMs", "commitEpochMs", "networkAtMs", "domAtMs")
_MODES = ("ag_ui", "langgraph_v3")
_ROUTE_METHODS = {
    "standard-command": "POST",
    "standard-attach": "GET",
    "v3-command": "POST",
    "v3-attach": "POST",
}


def _epoch_ms(value: object) -> float | int | None:
    if isinstance(value, (float, int)) and not isinstance(value, bool) and 0 < value <= 8.64e15:
        return value if math.isfinite(value) else None
    return None


def _observed_id(value: object) -> str | None:
    return _opaque_id(value) if isinstance(value, str) and 0 < len(value) <= 1024 else None


def _timing_rows(rows: object, keys: tuple[str, ...], merged: list[dict], errors: set[str]) -> None:
    seen = set()
    if not isinstance(rows, list):
        errors.add("missing_phase_rows")
        return
    for row in rows:
        ordinal = row.get("ordinal") if isinstance(row, dict) else None
        if type(ordinal) is not int or not 1 <= ordinal <= len(merged):
            errors.add("invalid_ordinal")
            continue
        if ordinal in seen:
            errors.add("duplicate_ordinal")
            continue
        seen.add(ordinal)
        for key in keys:
            merged[ordinal - 1][key] = _epoch_ms(row.get(key))


def _timing_routes(payload: dict, errors: set[str]) -> list[dict]:
    routes = []
    if not isinstance(payload.get("routes"), list):
        errors.add("missing_routes")
        return routes
    for raw in payload["routes"]:
        kind = raw.get("kind") if isinstance(raw, dict) else None
        if not isinstance(kind, str) or kind not in _ROUTE_METHODS:
            errors.add("unrecognized_route")
            continue
        method = raw.get("method")
        if method != _ROUTE_METHODS[kind]:
            errors.add("unexpected_route_method")
            method = None
        count = raw.get("frameCount")
        routes.append(
            {
                "kind": kind,
                "method": method,
                "requestAtMs": _epoch_ms(raw.get("requestAtMs")),
                "headersAtMs": _epoch_ms(raw.get("headersAtMs")),
                "publicRunId": _observed_id(raw.get("publicRunId")),
                "physicalRunId": _observed_id(raw.get("physicalRunId")),
                "frameCount": count if type(count) is int and count >= 0 else None,
            }
        )
    return routes


def _waterfall_attachment(result: dict, owned_root: Path, expected_mode: str) -> dict:
    matches = [a for a in result.get("attachments", []) if a.get("name") == "perf-waterfall"]
    try:
        if len(matches) != 1 or matches[0].get("contentType") != "application/json":
            raise ValueError("Expected one performance timing attachment")
        payload = json.loads(_attachment_body(matches[0], owned_root, "timing"))
        if (
            not isinstance(payload, dict)
            or type(payload.get("schemaVersion")) is not int
            or payload["schemaVersion"] != 1
        ):
            raise ValueError("Unsupported performance timing schema")
    except (ValueError, KeyError, TypeError, OSError):
        return {"complete": False, "errors": ["missing_or_invalid_waterfall_attachment"]}

    errors: set[str] = set()
    mode = payload.get("mode")
    if (
        mode not in _MODES
        or mode != expected_mode
        or payload.get("expectedMode", expected_mode) != expected_mode
    ):
        errors.add("transport_mode_unverified")
    projected = {
        "schemaVersion": 1,
        "mode": mode if mode in _MODES else "unknown",
        "expectedMode": expected_mode if expected_mode in _MODES else "unknown",
        "sampleId": _observed_id(payload.get("sampleId")),
        "conversationId": _observed_id(payload.get("conversationId")),
        "publicRunId": _observed_id(payload.get("publicRunId")),
        "physicalRunId": _observed_id(payload.get("physicalRunId")),
        "runIds": [
            identifier for raw in payload.get("runIds", []) if (identifier := _observed_id(raw))
        ]
        if isinstance(payload.get("runIds"), list)
        else [],
        "routes": _timing_routes(payload, errors),
    }
    for key in ("sampleId", "conversationId"):
        try:
            UUID(projected[key] or "")
        except ValueError:
            errors.add("missing_or_invalid_identity")
    if expected_mode == "ag_ui" and not projected["publicRunId"]:
        errors.add("missing_public_run_id")
    stream_kinds = (
        {"standard-command", "standard-attach"} if expected_mode == "ag_ui" else {"v3-attach"}
    )
    command_kind = "standard-command" if expected_mode == "ag_ui" else "v3-command"
    if not any(r["kind"] == command_kind and r["requestAtMs"] for r in projected["routes"]):
        errors.add("missing_command_route_evidence")
    if not any(
        r["kind"] in stream_kinds and r["frameCount"] and r["requestAtMs"] and r["headersAtMs"]
        for r in projected["routes"]
    ):
        errors.add("missing_stream_route_evidence")
    for key in ("submittedAtMs", "firstFrameAtMs", "lastFrameAtMs"):
        projected[key] = _epoch_ms(payload.get(key))
        if projected[key] is None:
            errors.add("missing_browser_timestamp")
    if (
        projected["firstFrameAtMs"]
        and projected["lastFrameAtMs"]
        and projected["firstFrameAtMs"] > projected["lastFrameAtMs"]
    ):
        errors.add("invalid_frame_order")
    issues = payload.get("parserIssues")
    projected["parserIssues"] = issues if type(issues) is int and issues >= 0 else None
    if projected["parserIssues"] != 0:
        errors.add("network_parser_incomplete")

    rows = [{"ordinal": n, **dict.fromkeys(_PHASES)} for n in range(1, 7)]
    backend = payload.get("backendTiming")
    backend = backend if isinstance(backend, dict) else {}
    _timing_rows(backend.get("model"), ("sourceEpochMs",), rows, errors)
    _timing_rows(backend.get("journal"), ("commitEpochMs",), rows, errors)
    _timing_rows(payload.get("ordinals"), ("networkAtMs", "domAtMs"), rows, errors)
    missing = {key: [row["ordinal"] for row in rows if row[key] is None] for key in _PHASES}
    if any(missing.values()):
        errors.add("incomplete_phase_observations")
    # One transaction, network packet or DOM mutation may deliver several ordinal values together.
    # V3 can publish before a journal transaction commits, so commit -> network is a signed delta.
    for key in _PHASES:
        present = [row[key] for row in rows if row[key] is not None]
        if present != sorted(present):
            errors.add("phase_ordinal_time_reversed")
    observer_errors = backend.get("errors")
    observer_codes = []
    if observer_errors != []:
        errors.add("backend_observer_incomplete")
        allowed = {
            "backend_observer_reported_errors",
            "backend_observer_failed",
            "backend_observer_unavailable",
        }
        if isinstance(observer_errors, list):
            observer_codes = [
                e if isinstance(e, str) and e in allowed else "unrecognized"
                for e in observer_errors
            ]
    return {
        **projected,
        "backendTiming": {
            "model": [{"ordinal": r["ordinal"], "sourceEpochMs": r["sourceEpochMs"]} for r in rows],
            "journal": [
                {"ordinal": r["ordinal"], "commitEpochMs": r["commitEpochMs"]} for r in rows
            ],
            "errors": observer_codes,
            "errorCount": len(observer_errors) if isinstance(observer_errors, list) else None,
        },
        "ordinals": rows,
        "missingOrdinals": missing,
        "errors": sorted(errors),
        "complete": not errors,
    }


def _case(spec: dict, test: dict, owned_root: Path, group: str, performance_timing: bool) -> dict:
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
            if (
                filename == "chat-langgraph-v3-regressions.spec.ts"
                and title == "resets token usage state when switching between conversations"
                and group in {"ordinary-regressions-ag_ui", "ordinary-regressions-langgraph_v3"}
            ):
                annotations = result.get("annotations", [])
                if len(results) == 1:
                    annotations = [*annotations, *test.get("annotations", [])]
                attempt["network"] = _usage_network_annotations(annotations)
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
            if performance_timing:
                waterfall = _waterfall_attachment(
                    result, owned_root, group.removeprefix("performance-")
                )
                attempt["waterfall"] = waterfall
                if not waterfall["complete"]:
                    errors.append("incomplete_performance_waterfall")
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


def playwright_report(
    data: dict, owned_root: Path, *, group: str, specs: list[str], performance_timing: bool = False
) -> dict:
    """Check a pinned Playwright report for single-attempt, verified passes."""
    requested = {_spec_selector(path) for path in specs}
    cases = []
    declarations: list[tuple[str, tuple[int, ...]]] = []
    for suite in data.get("suites", []):
        for spec, locations in _specs(suite):
            for test in spec.get("tests", []):
                case = _case(spec, test, owned_root, group, performance_timing)
                if type(spec.get("line")) is int:
                    case["declaration_line"] = spec["line"]
                cases.append(case)
                declarations.append((case["file"], locations))
    errors = []
    counts = {outcome: sum(c["outcome"] == outcome for c in cases) for outcome in OUTCOMES}
    if not cases or {f for f, _ in declarations} != {path for path, _ in requested}:
        errors.append("missing_or_unexpected_spec")
    if any(
        line is not None and not any(file == path and line in lines for file, lines in declarations)
        for path, line in requested
    ) or any(
        (file, None) not in requested and not any((file, line) in requested for line in lines)
        for file, lines in declarations
    ):
        errors.append("missing_or_unexpected_spec_line")
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
