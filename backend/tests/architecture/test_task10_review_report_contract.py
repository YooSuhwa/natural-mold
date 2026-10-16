from collections.abc import Callable
from pathlib import Path

import pytest

from scripts.task10_review_report import (
    I1_REVIEW_CHECKS,
    I24_REVIEW_CHECKS,
    ReviewGrammar,
    ReviewHashFacts,
    ReviewReportError,
    grammar_from_registered_stage,
    parse_review_report,
    review_input_document,
    reviewer_instruction,
)

I1_CHECKS = (
    "GENESIS_CHAIN",
    "PREEDIT_SCOPE_RELEASE",
    "LOCATOR_ROLLBACK",
    "ATOMIC_PUBLICATION",
    "ROLE_SCHEMA",
    "ACTUAL_ROOT_GIT",
    "STRUCTURAL_AUTHORITY",
    "REVIEW_GRAMMAR",
    "NATIVE_FINAL_AUTHORITY",
    "TEST_NODE_PRESERVATION",
    "FIXTURE_INDEPENDENCE",
    "CAP_PATH_SCOPE",
)
I24_CHECKS = (
    "R1_R8",
    "FIVE_ENTRIES",
    "ARCHIVE_PRECEDENCE",
    "B08_COMPATIBILITY",
    "CHILD_LOCK_RELEASE",
    "GOOGLE_LEASE_ONLY",
    "GOOGLE_EXACT_CLIENT",
    "GOOGLE_DEFAULT_PRESERVED",
    "GOOGLE_CLEANUP_ONCE",
    "GOOGLE_NO_AIOHTTP",
    "STOCK_TASK_TOOL",
    "ZERO_TOOL_INTERCEPTOR",
    "FROZEN_RESPONSE_VARIANT",
    "POST_MARKER_UNREACHABLE",
    "TOOL_RUNTIME_INVOCATION_ID",
    "NO_LEAF_LOCK_ACROSS_CHILD_AWAIT",
    "OWNERSHIP_ACYCLIC",
    "FACADE_PUBLIC_BEHAVIOR",
    "LIFECYCLE_WIRING",
    "PROVIDER_SDK_RETRIES",
    "CURSOR_INVALIDATION_REUSE",
    "ALL_CONSUMER_MIGRATION",
)
FACTS = ReviewHashFacts("a" * 64, "b" * 64, "c" * 64)


def _report(headers: tuple[str, ...], checks: tuple[str, ...]) -> str:
    return (
        "\n".join((*headers, *(f"CHECK: {check}: PASS" for check in checks), "FINDINGS: none"))
        + "\n"
    )


def _i1_report() -> str:
    return _report(
        (
            f"PLAN_SHA256: {FACTS.plan_sha256}",
            f"CANDIDATE_MANIFEST_SHA256: {FACTS.candidate_manifest_sha256}",
            f"BINDING_SHA256: {FACTS.binding_sha256}",
        ),
        I1_CHECKS,
    )


def _i24_report() -> str:
    return _report(
        ("VERDICT: APPROVE", f"BINDING_SHA256: {FACTS.binding_sha256}", "OPEN_CRITICAL_HIGH: 0"),
        I24_CHECKS,
    )


def _path(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "report.txt"
    path.write_text(text, encoding="utf-8", newline="")
    return path


@pytest.mark.parametrize(
    ("stage", "canonical"),
    [
        ("I1_EXACT_COMMIT_REVIEW_RAW_RECEIVED", _i1_report()),
        ("I24_REVIEW_RAW_RECEIVED", _i24_report()),
    ],
)
def test_i1_and_i24_canonical_reports_are_byte_exact(
    tmp_path: Path, stage: str, canonical: str
) -> None:
    grammar = grammar_from_registered_stage(stage)

    report = parse_review_report(_path(tmp_path, canonical), FACTS, grammar)

    assert (I1_REVIEW_CHECKS, I24_REVIEW_CHECKS) == (I1_CHECKS, I24_CHECKS)
    assert (report.verdict, report.open_critical_high, report.findings) == ("APPROVE", 0, "none")
    assert reviewer_instruction(grammar, FACTS) == canonical


def test_stage_derived_grammar_orders_closed_review_input() -> None:
    i1 = grammar_from_registered_stage("I1_POSTCOMMIT_CANDIDATE")
    i24 = grammar_from_registered_stage("I23_FREEZE_READY")

    assert review_input_document("d" * 40, {}, i1)["required_checks"] == list(I1_CHECKS)
    assert review_input_document("d" * 40, {}, i24)["required_checks"] == list(I24_CHECKS)
    with pytest.raises(ReviewReportError, match="review_grammar_unregistered"):
        review_input_document("d" * 40, {}, ReviewGrammar("i1", "caller", ("CALLER",)))


def _rejection(tmp_path: Path, text: str, stage: str, facts: ReviewHashFacts = FACTS) -> str:
    with pytest.raises(ReviewReportError) as raised:
        parse_review_report(_path(tmp_path, text), facts, grammar_from_registered_stage(stage))
    return str(raised.value)


def _reorder(text: str) -> str:
    first = "CHECK: GENESIS_CHAIN: PASS"
    second = "CHECK: PREEDIT_SCOPE_RELEASE: PASS"
    return text.replace(f"{first}\n{second}", f"{second}\n{first}")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda text: "prefix\n" + text,
        lambda text: text + "suffix\n",
        lambda text: text.removesuffix("\n"),
        lambda text: text + "\n",
        lambda text: text.replace("\n", "\r\n"),
        lambda text: text.replace("FINDINGS", "\nFINDINGS"),
        lambda text: text.replace(
            "FINDINGS: none\n", "CHECK: GENESIS_CHAIN: PASS\nFINDINGS: none\n"
        ),
        _reorder,
        lambda text: text.replace("CHECK: LOCATOR_ROLLBACK: PASS\n", ""),
        lambda text: text.replace("GENESIS_CHAIN", "UNKNOWN_CHECK"),
        lambda text: text.replace("FINDINGS: none", "FINDINGS: open"),
    ],
    ids=[
        "prefix",
        "trailing",
        "missing_terminal_lf",
        "duplicate_final_lf",
        "crlf",
        "blank",
        "duplicate",
        "reordered",
        "omitted",
        "unknown",
        "findings",
    ],
)
def test_i1_prefix_trailing_and_check_mutations_reject_stably(
    tmp_path: Path, mutate: Callable[[str], str]
) -> None:
    mutated = mutate(_i1_report())

    assert _rejection(tmp_path, mutated, "I1_EXACT_COMMIT_REVIEW_RAW_RECEIVED") == _rejection(
        tmp_path, mutated, "I1_EXACT_COMMIT_REVIEW_RAW_RECEIVED"
    )


def test_binding_parent_open_and_wrong_stage_reports_reject(tmp_path: Path) -> None:
    for facts in (
        ReviewHashFacts(FACTS.plan_sha256, FACTS.candidate_manifest_sha256, "d" * 64),
        ReviewHashFacts("d" * 64, FACTS.candidate_manifest_sha256, FACTS.binding_sha256),
        ReviewHashFacts(FACTS.plan_sha256, "d" * 64, FACTS.binding_sha256),
    ):
        with pytest.raises(ReviewReportError):
            parse_review_report(
                _path(tmp_path, _i1_report()),
                facts,
                grammar_from_registered_stage("I1_EXACT_COMMIT_REVIEW_RAW_RECEIVED"),
            )
    with pytest.raises(ReviewReportError):
        parse_review_report(
            _path(
                tmp_path, _i24_report().replace("OPEN_CRITICAL_HIGH: 0", "OPEN_CRITICAL_HIGH: 1")
            ),
            FACTS,
            grammar_from_registered_stage("I24_REVIEW_RAW_RECEIVED"),
        )
    with pytest.raises(ReviewReportError):
        parse_review_report(
            _path(tmp_path, _i1_report()),
            FACTS,
            grammar_from_registered_stage("I24_REVIEW_RAW_RECEIVED"),
        )
    invalid = tmp_path / "invalid-utf8.txt"
    invalid.write_bytes(b"\xff")
    with pytest.raises(ReviewReportError, match="review_report_utf8"):
        parse_review_report(
            invalid, FACTS, grammar_from_registered_stage("I1_EXACT_COMMIT_REVIEW_RAW_RECEIVED")
        )
