"""Build bounded structural diagnostics from failed PostgreSQL child output."""

from __future__ import annotations

import hashlib
import re
from typing import Final, TypedDict

STRUCTURAL_RECORD_LIMIT: Final = 64
STRUCTURAL_RECORD_CHAR_LIMIT: Final = 512
SCHEMA_IDENTIFIER_LIMIT: Final = 16
_LOCATION: Final = re.compile(
    r"^(?P<path>(?:tests|app|scripts)/[A-Za-z0-9_./-]+\.py):"
    r"(?P<line>[1-9][0-9]*): in (?P<function>[A-Za-z_][A-Za-z0-9_]*)$"
)
_EXCEPTION: Final = re.compile(
    r"^(?P<path>(?:tests|app|scripts)/[A-Za-z0-9_./-]+\.py):"
    r"(?P<line>[1-9][0-9]*): (?P<exception>[A-Za-z_][A-Za-z0-9_.]*"
    r"(?:Error|Exception|Failed|Failure))(?:[: ].*)?$"
)
_FAILED: Final = re.compile(
    r"^FAILED (?P<path>(?:tests|app|scripts)/[A-Za-z0-9_./-]+\.py)::"
    r"(?P<test>[A-Za-z_][A-Za-z0-9_]*)(?:\[[^]]*\])?"
    r"(?: - (?P<exception>[A-Za-z_][A-Za-z0-9_.]*"
    r"(?:Error|Exception|Failed|Failure))(?:[: ].*)?)?$"
)
_MISSING_COLUMN: Final = re.compile(
    r'\bcolumn "(?P<column>[a-z][a-z0-9_]{0,62})"'
    r'(?: of relation "(?P<relation>[a-z][a-z0-9_]{0,62})")? does not exist\b'
)
_PYTEST_EXCEPTION_GROUP_PREFIX: Final = re.compile(r"^(?:\|[ \t]*)+")


class ChildFailureDiagnostics(TypedDict):
    schema_version: int
    raw_output_chars: int
    raw_output_sha256: str
    raw_output_retained: bool
    structural_record_limit: int
    structural_record_char_limit: int
    structural_record_count: int
    omitted_structural_record_count: int
    structural_records_truncated: bool
    record_text_truncated_count: int
    records: list[str]
    schema_identifier_limit: int
    schema_identifier_count: int
    omitted_schema_identifier_count: int
    schema_identifiers_truncated: bool
    schema_identifiers: list[str]


def _structural_record(line: str) -> str | None:
    line = _PYTEST_EXCEPTION_GROUP_PREFIX.sub("", line, count=1)
    location = _LOCATION.fullmatch(line)
    if location is not None:
        return f"{location.group('path')}:{location.group('line')}:in {location.group('function')}"
    exception = _EXCEPTION.fullmatch(line)
    if exception is not None:
        return f"{exception.group('path')}:{exception.group('line')}:{exception.group('exception')}"
    failed = _FAILED.fullmatch(line)
    if failed is None:
        return None
    record = f"FAILED {failed.group('path')}::{failed.group('test')}"
    exception_name = failed.group("exception")
    return f"{record} - {exception_name}" if exception_name is not None else record


def build_child_failure_diagnostics(output: str) -> ChildFailureDiagnostics:
    candidates = [
        record
        for line in output.splitlines()
        if (record := _structural_record(line.strip())) is not None
    ]
    retained = candidates[:STRUCTURAL_RECORD_LIMIT]
    bounded = [record[:STRUCTURAL_RECORD_CHAR_LIMIT] for record in retained]
    truncated_text_count = sum(len(record) > STRUCTURAL_RECORD_CHAR_LIMIT for record in retained)
    omitted_count = len(candidates) - len(retained)
    schema_candidates: list[str] = []
    for match in _MISSING_COLUMN.finditer(output):
        schema_candidates.append(f"column:{match.group('column')}")
        relation = match.group("relation")
        if relation is not None:
            schema_candidates.append(f"relation:{relation}")
    unique_schema_identifiers = list(dict.fromkeys(schema_candidates))
    schema_identifiers = unique_schema_identifiers[:SCHEMA_IDENTIFIER_LIMIT]
    omitted_schema_identifiers = len(unique_schema_identifiers) - len(schema_identifiers)
    return {
        "schema_version": 1,
        "raw_output_chars": len(output),
        "raw_output_sha256": hashlib.sha256(output.encode()).hexdigest(),
        "raw_output_retained": False,
        "structural_record_limit": STRUCTURAL_RECORD_LIMIT,
        "structural_record_char_limit": STRUCTURAL_RECORD_CHAR_LIMIT,
        "structural_record_count": len(bounded),
        "omitted_structural_record_count": omitted_count,
        "structural_records_truncated": omitted_count > 0,
        "record_text_truncated_count": truncated_text_count,
        "records": bounded,
        "schema_identifier_limit": SCHEMA_IDENTIFIER_LIMIT,
        "schema_identifier_count": len(schema_identifiers),
        "omitted_schema_identifier_count": omitted_schema_identifiers,
        "schema_identifiers_truncated": omitted_schema_identifiers > 0,
        "schema_identifiers": schema_identifiers,
    }
