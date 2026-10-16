"""Stage-derived whole-file grammars for Task 10 reviewer reports."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, assert_never

type JsonValue = str | int | float | bool | None | Sequence["JsonValue"] | Mapping[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]

I1_REVIEW_CHECKS: Final = (
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
I24_REVIEW_CHECKS: Final = (
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
HEX64: Final = re.compile(r"[0-9a-f]{64}")


class ReviewReportError(ValueError):
    """Raised when a reviewer report does not match its registered grammar."""


@dataclass(frozen=True, slots=True)
class ReviewHashFacts:
    """Registered digest facts required by a stage-specific report."""

    plan_sha256: str
    candidate_manifest_sha256: str
    binding_sha256: str


@dataclass(frozen=True, slots=True)
class ReviewGrammar:
    """Closed report tuple selected from a validated authority lifecycle stage."""

    kind: Literal["i1", "i24"]
    purpose: str
    checks: tuple[str, ...]


I1_GRAMMAR: Final = ReviewGrammar("i1", "exact_commit", I1_REVIEW_CHECKS)
I24_GRAMMAR: Final = ReviewGrammar("i24", "parent_review", I24_REVIEW_CHECKS)
I1_STAGES: Final = frozenset(
    {
        "I1_POSTCOMMIT_CANDIDATE",
        "I1_REVIEW_MATERIAL_FROZEN",
        "I1_EXACT_COMMIT_REVIEW_INPUT",
        "I1_EXACT_COMMIT_REVIEW_RAW_RECEIVED",
        "I1_EXACT_COMMIT_REVIEW_ACCEPTED",
    }
)
I24_STAGES: Final = frozenset(
    {
        "I23_FREEZE_READY",
        "PARENT_REVIEW_MATERIAL_FROZEN",
        "I24_REVIEW_INPUT",
        "I24_REVIEW_RAW_RECEIVED",
        "I24_REVIEW_ACCEPTED",
    }
)


def grammar_from_registered_stage(stage: str) -> ReviewGrammar:
    """Return the sole grammar legal for a Root-validated lifecycle stage."""
    if stage in I1_STAGES:
        return I1_GRAMMAR
    if stage in I24_STAGES:
        return I24_GRAMMAR
    raise ReviewReportError("review_stage_unsupported")


def _registered(grammar: ReviewGrammar) -> ReviewGrammar:
    if grammar is I1_GRAMMAR or grammar is I24_GRAMMAR:
        return grammar
    raise ReviewReportError("review_grammar_unregistered")


def review_input_document(
    candidate: str, roles: Mapping[str, JsonValue], grammar: ReviewGrammar
) -> JsonObject:
    """Create the closed runtime review-input document from the selected grammar."""
    grammar = _registered(grammar)
    return {
        "kind": "task10-review-input",
        "candidate": candidate,
        "roles": roles,
        "required_checks": list(grammar.checks),
    }


@dataclass(frozen=True, slots=True)
class ReviewReport:
    """Terminal facts derived from a fully matched reviewer report."""

    verdict: str
    binding_sha256: str
    open_critical_high: int
    findings: str


def _headers(grammar: ReviewGrammar, facts: ReviewHashFacts) -> tuple[str, ...]:
    match grammar.kind:
        case "i1":
            return (
                f"PLAN_SHA256: {facts.plan_sha256}",
                f"CANDIDATE_MANIFEST_SHA256: {facts.candidate_manifest_sha256}",
                f"BINDING_SHA256: {facts.binding_sha256}",
            )
        case "i24":
            return (
                "VERDICT: APPROVE",
                f"BINDING_SHA256: {facts.binding_sha256}",
                "OPEN_CRITICAL_HIGH: 0",
            )
        case unreachable:
            assert_never(unreachable)


def _lines(grammar: ReviewGrammar, facts: ReviewHashFacts) -> tuple[str, ...]:
    return (
        *_headers(grammar, facts),
        *(f"CHECK: {check}: PASS" for check in grammar.checks),
        "FINDINGS: none",
    )


def reviewer_instruction(grammar: ReviewGrammar, facts: ReviewHashFacts) -> str:
    """Render exactly one terminal-LF report grammar for the independent reviewer."""
    grammar = _registered(grammar)
    _validate_facts(grammar, facts)
    return "\n".join(_lines(grammar, facts)) + "\n"


def _validate_facts(grammar: ReviewGrammar, facts: ReviewHashFacts) -> None:
    values = (facts.binding_sha256,)
    if grammar.kind == "i1":
        values = (facts.plan_sha256, facts.candidate_manifest_sha256, facts.binding_sha256)
    if any(HEX64.fullmatch(value) is None for value in values):
        raise ReviewReportError("review_hash_facts_invalid")


def parse_review_report(path: Path, facts: ReviewHashFacts, grammar: ReviewGrammar) -> ReviewReport:
    """Parse an exact UTF-8/LF report selected by validated Root authority."""
    grammar = _registered(grammar)
    _validate_facts(grammar, facts)
    raw = path.read_bytes()
    if b"\r" in raw:
        raise ReviewReportError("review_report_crlf")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReviewReportError("review_report_utf8") from exc
    if not text.endswith("\n") or text.endswith("\n\n"):
        raise ReviewReportError("review_report_termination")
    if tuple(text[:-1].split("\n")) != _lines(grammar, facts):
        raise ReviewReportError("review_report_grammar_mismatch")
    return ReviewReport("APPROVE", facts.binding_sha256, 0, "none")
