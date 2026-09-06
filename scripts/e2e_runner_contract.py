"""Typed trust-boundary contracts for the isolated E2E runner."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, NamedTuple
from urllib.parse import quote

from sqlalchemy.engine import URL, make_url

Lane = Literal["scripted", "live"]
Project = Literal["scripted-smoke", "scripted-full", "scripted-capture", "live-manual"]
SelfTest = Literal["normal", "spec-failure", "server-failure", "dsn-failure", "sigint"]

FINAL_CAPTURE_SPECS = (
    "e2e/agent-settings.spec.ts",
    "e2e/runtime-todo-policy.spec.ts",
    "e2e/runtime-filesystem-policy.spec.ts",
    "e2e/chat-compaction.spec.ts",
)


@dataclass(frozen=True, slots=True)
class FinalE2eRun:
    attempt_id: str
    head_sha: str
    requested_specs: tuple[str, ...]


class E2eDsns(NamedTuple):
    async_url: str
    sync_url: str
    integration_url: str


@dataclass(frozen=True, slots=True)
class E2eTarget:
    user: str
    password: str
    port: int
    database: str


class E2eContractError(RuntimeError):
    """Expose a stable reason without reflecting credential-bearing input."""


PROJECT_LANE: dict[Project, Lane] = {
    "scripted-smoke": "scripted",
    "scripted-full": "scripted",
    "scripted-capture": "scripted",
    "live-manual": "live",
}
DEFAULT_PROJECT: dict[Lane, Project] = {
    "scripted": "scripted-full",
    "live": "live-manual",
}
LANE_PORTS: dict[Lane, tuple[int, int]] = {
    "scripted": (3100, 8101),
    "live": (3200, 8201),
}


def parse_lane(raw: str) -> Lane:
    match raw:
        case "scripted":
            return "scripted"
        case "live":
            return "live"
        case _:
            raise E2eContractError("invalid_lane") from None


def parse_self_test(raw: str) -> SelfTest:
    match raw:
        case "normal":
            return "normal"
        case "spec-failure":
            return "spec-failure"
        case "server-failure":
            return "server-failure"
        case "dsn-failure":
            return "dsn-failure"
        case "sigint":
            return "sigint"
        case _:
            raise E2eContractError("invalid_self_test") from None


def parse_project(raw: str, lane: Lane) -> Project:
    match raw:
        case "scripted-smoke":
            project: Project = "scripted-smoke"
        case "scripted-full":
            project = "scripted-full"
        case "scripted-capture":
            project = "scripted-capture"
        case "live-manual":
            project = "live-manual"
        case _:
            raise E2eContractError("invalid_project") from None
    if PROJECT_LANE[project] != lane:
        raise E2eContractError("project_lane_mismatch")
    return project


def validate_forwarded_arguments(arguments: tuple[str, ...], lane: Lane) -> tuple[str, ...]:
    policy = {"--workers=1", "--retries=0"}
    forwarded = tuple(argument for argument in arguments if argument in policy)
    if len(forwarded) != len(set(forwarded)):
        raise E2eContractError("playwright_option_override_forbidden")
    selections = tuple(argument for argument in arguments if argument not in policy)
    if any(argument.startswith("-") for argument in selections):
        raise E2eContractError("playwright_option_override_forbidden")
    if lane == "live" and selections:
        raise E2eContractError("live_selection_override_forbidden")
    for argument in selections:
        normalized = PurePosixPath(argument)
        if (
            "\\" in argument
            or normalized.is_absolute()
            or normalized.parts[:1] != ("e2e",)
            or ".." in normalized.parts
            or not argument.endswith(".spec.ts")
            or any(not part or part.startswith(".") for part in normalized.parts)
        ):
            raise E2eContractError("playwright_selection_path_forbidden")
    return selections


def parse_final_e2e_run(
    destination: Path,
    *,
    lane: Lane,
    project: Project,
    requested_specs: tuple[str, ...],
    export_slug: str | None,
    attempt_id: str | None,
    head_sha: str | None,
) -> FinalE2eRun | None:
    """Bind the three final E2E filenames to one exact lane, project, selection, and slug."""
    if attempt_id is None:
        return None
    f2_match = re.fullmatch(
        r"f2-static\.([a-z][a-z0-9-]{0,63})\.([0-9a-f]{16})\.json",
        destination.name,
    )
    if f2_match is not None:
        from project_gate_catalog import CATALOG  # noqa: PLC0415 - optional producer contract

        node = CATALOG.get(f2_match.group(1))
        if (
            node is None
            or node.kind != "e2e"
            or lane != "scripted"
            or project != node.argv[0]
            or requested_specs != node.argv[1:]
            or head_sha is None
        ):
            raise E2eContractError("final_manifest_selection_mismatch")
        expected_slug = f"runtime-policy-final-{attempt_id}-f2-{f2_match.group(2)}"
        if export_slug != expected_slug:
            raise E2eContractError("final_export_slug_mismatch")
        return FinalE2eRun(attempt_id, head_sha, requested_specs)
    expected = {
        "f3-scripted.json": ("scripted", "scripted-full", (), "scripted"),
        "f3-capture.json": ("scripted", "scripted-capture", FINAL_CAPTURE_SPECS, "capture"),
        "f3-live.json": ("live", "live-manual", (), "live"),
    }.get(destination.name)
    if expected is None or head_sha is None:
        raise E2eContractError("final_manifest_name_forbidden")
    expected_lane, expected_project, expected_specs, suffix = expected
    if (lane, project, requested_specs) != (expected_lane, expected_project, expected_specs):
        raise E2eContractError("final_manifest_selection_mismatch")
    if export_slug != f"runtime-policy-final-{attempt_id}-{suffix}":
        raise E2eContractError("final_export_slug_mismatch")
    return FinalE2eRun(attempt_id, head_sha, requested_specs)


def build_e2e_dsns(*, password: str, port: int, database: str) -> E2eDsns:
    authority = f"runner:{quote(password, safe='')}@127.0.0.1:{port}/{database}"
    return E2eDsns(
        async_url=f"postgresql+asyncpg://{authority}",
        sync_url=f"postgresql://{authority}",
        integration_url=f"postgresql+psycopg://{authority}",
    )


def _parse_url(raw: str, driver: str) -> E2eTarget:
    try:
        url: URL = make_url(raw)
    except (TypeError, ValueError, AttributeError) as error:
        raise E2eContractError("malformed_dsn") from error
    if url.drivername != driver:
        raise E2eContractError("dsn_driver")
    if url.query or url.host != "127.0.0.1" or url.port is None:
        raise E2eContractError("dsn_target")
    if url.username != "runner" or url.password is None or url.database is None:
        raise E2eContractError("dsn_component")
    if not url.database.startswith(("moldy_e2e_scripted_", "moldy_e2e_live_")):
        raise E2eContractError("unsafe_database")
    if not url.database.replace("_", "").isalnum() or not url.database.islower():
        raise E2eContractError("unsafe_database")
    return E2eTarget(url.username, url.password, url.port, url.database)


def parse_e2e_dsns(dsns: E2eDsns, lane: Lane) -> E2eTarget:
    targets = (
        _parse_url(dsns.async_url, "postgresql+asyncpg"),
        _parse_url(dsns.sync_url, "postgresql"),
        _parse_url(dsns.integration_url, "postgresql+psycopg"),
    )
    if targets[0] != targets[1] or targets[0] != targets[2]:
        raise E2eContractError("dsn_mismatch")
    if not targets[0].database.startswith(f"moldy_e2e_{lane}_"):
        raise E2eContractError("lane_database_mismatch")
    return targets[0]
