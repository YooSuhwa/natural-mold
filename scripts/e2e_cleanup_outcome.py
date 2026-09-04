"""Logical E2E execution, cleanup, and aggregate egress validation."""

from __future__ import annotations

from urllib.parse import urlsplit

from e2e_cleanup_contract import (
    CLEANUP_FIELDS,
    LIVE_NODES,
    integer,
    mapping,
    require,
    string,
    strings,
)
from postgres_cleanup_checker import ManifestValidationError


def validate_cleanup(payload: dict[str, object], database_owned: bool) -> None:
    """Require every teardown claim and truthful foreign-container preservation."""
    cleanup = mapping(payload.get("cleanup"), "cleanup")
    require(all(cleanup.get(name) is True for name in CLEANUP_FIELDS), "cleanup")
    foreign = cleanup.get("foreign_containers_preserved")
    require(foreign is True if database_owned else foreign is None, "foreign_container")


def validate_egress(value: object, lane: str) -> None:
    """Accept only redacted aggregate live egress or the fixed scripted empty receipt."""
    egress = mapping(value, "egress")
    if lane == "scripted":
        require(egress == {"enabled": False, "clean_stop": True, "records": []}, "egress")
        return
    require(egress.get("enabled") is True and egress.get("clean_stop") is True, "egress")
    records = egress.get("records")
    if not isinstance(records, list):
        raise ManifestValidationError("egress_records")
    require(0 < len(records) <= 100, "egress_records")
    for record_value in records:
        record = mapping(record_value, "egress_records")
        require(
            set(record) == {"method", "origin", "path_class", "status", "count"},
            "egress_record",
        )
        parsed = urlsplit(string(record.get("origin"), "egress_record"))
        require(
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
            and not parsed.path
            and not parsed.query
            and not parsed.fragment,
            "egress_record",
        )
        require(
            record.get("method") == "POST"
            and record.get("path_class") == "chat_completions"
            and 200 <= integer(record.get("status"), "egress_record") < 300
            and integer(record.get("count"), "egress_record") >= 1,
            "egress_record",
        )


def validate_outcome(
    payload: dict[str, object], lane: str, project: str, *, source_rejected: bool
) -> None:
    """Validate success, forced-failure, and signal receipt semantics."""
    status = string(payload.get("status"), "status")
    self_test = string(payload.get("self_test"), "self_test")
    exit_code = integer(payload.get("child_exit_code"), "child_exit")
    reason = payload.get("failure_reason")
    selected = strings(payload.get("selected_ids"), "selected_ids")
    executed = strings(payload.get("executed_ids"), "executed_ids")
    require(
        len(selected) == len(set(selected)) and len(executed) == len(set(executed)),
        "selected_ids",
    )
    require(
        all(node.startswith(f"{project}::e2e/") for node in (*selected, *executed)),
        "project_selection",
    )
    if project == "scripted-smoke":
        require(
            all(
                node.startswith("scripted-smoke::e2e/smoke.spec.ts::")
                for node in (*selected, *executed)
            ),
            "smoke_selection",
        )
    if status == "passed":
        require(
            self_test == "normal" and reason is None and exit_code == 0 and not source_rejected,
            "status",
        )
        require(bool(selected) and selected == executed, "node_execution_mismatch")
        if lane == "live":
            require(selected == list(LIVE_NODES), "live_selection")
        return
    if self_test == "normal":
        playwright_failed = reason == "playwright_failed" and exit_code == 1
        artifact_export_failed = (
            source_rejected and reason == "artifact_export_failed" and exit_code == 0
        )
        require(
            status == "failed" and (playwright_failed or artifact_export_failed),
            "self_test",
        )
        require(bool(selected) and selected == executed, "node_execution_mismatch")
        if lane == "live":
            require(selected == list(LIVE_NODES), "live_selection")
        return
    if self_test == "sigint":
        require(
            status == "interrupted" and reason == "signal" and exit_code in {130, 143},
            "self_test",
        )
        require(not executed, "node_execution_mismatch")
        return
    match self_test:
        case "spec-failure":
            require(
                status == "failed" and reason == "playwright_list_failed" and exit_code == 70,
                "self_test",
            )
        case "server-failure":
            require(
                status == "failed" and reason == "server_start_failed" and 0 < exit_code < 128,
                "self_test",
            )
        case "dsn-failure":
            require(
                status == "failed" and reason == "dsn_mismatch" and exit_code == 70,
                "self_test",
            )
        case _:
            raise ManifestValidationError("self_test")
    require(not executed, "node_execution_mismatch")
