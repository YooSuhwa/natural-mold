"""Trust-boundary tests for the isolated E2E runner."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from e2e_runner_contract import (  # noqa: E402
    E2eContractError,
    E2eDsns,
    build_e2e_dsns,
    parse_e2e_dsns,
    parse_project,
    validate_forwarded_arguments,
)


def test_project_rejects_lane_mismatch() -> None:
    """Given a live project in scripted, when parsed, then startup fails."""
    with pytest.raises(E2eContractError, match="project_lane_mismatch"):
        parse_project("live-manual", "scripted")


@pytest.mark.parametrize(
    "argument",
    ["--workers=2", "--retries", "--project=chromium", "--reuse-existing-server"],
)
def test_forwarded_arguments_reject_weakened_execution(argument: str) -> None:
    """Given an ownership override, when parsed, then it is rejected."""
    with pytest.raises(E2eContractError):
        validate_forwarded_arguments((argument,), "scripted")


def test_forwarded_arguments_allow_only_scripted_relative_spec_paths() -> None:
    """Given a safe subset path, when parsed, then only scripted accepts it."""
    assert validate_forwarded_arguments(("e2e/chat.spec.ts",), "scripted") == ("e2e/chat.spec.ts",)
    with pytest.raises(E2eContractError, match="live_selection_override_forbidden"):
        validate_forwarded_arguments(("e2e/chat.spec.ts",), "live")
    with pytest.raises(E2eContractError, match="playwright_selection_path_forbidden"):
        validate_forwarded_arguments(("e2e/../outside.spec.ts",), "scripted")


def test_forwarded_arguments_consume_exact_redundant_execution_policy() -> None:
    """Given roadmap-fixed flags, when parsed, then they are consumed rather than forwarded."""
    assert validate_forwarded_arguments(
        ("e2e/chat.spec.ts", "--workers=1", "--retries=0"), "scripted"
    ) == ("e2e/chat.spec.ts",)


@pytest.mark.parametrize("argument", ["--workers=0", "--workers=2", "--retries=1"])
def test_forwarded_arguments_reject_noncanonical_execution_policy(argument: str) -> None:
    """Given a noncanonical policy flag, when parsed, then startup fails closed."""
    with pytest.raises(E2eContractError, match="playwright_option_override_forbidden"):
        validate_forwarded_arguments((argument,), "scripted")


def test_dsns_require_matching_lane_target() -> None:
    """Given mismatched DSNs, when parsed, then no connection-ready target exists."""
    safe = build_e2e_dsns(password="secret", port=54321, database="moldy_e2e_scripted_ab12")
    mismatched = E2eDsns(
        safe.async_url,
        safe.sync_url.replace("ab12", "cd34"),
        safe.integration_url,
    )

    with pytest.raises(E2eContractError, match="dsn_mismatch"):
        parse_e2e_dsns(mismatched, "scripted")


def test_dsns_round_trip_without_reflecting_secret() -> None:
    """Given encoded credentials, when parsed, then the typed target is preserved."""
    dsns = build_e2e_dsns(
        password="secret:/?#[]@",
        port=54321,
        database="moldy_e2e_live_ab12",
    )

    target = parse_e2e_dsns(dsns, "live")

    assert target.port == 54321
    assert target.database == "moldy_e2e_live_ab12"
