"""Finite, secret-free Playwright network failure annotations."""

from __future__ import annotations

from enum import StrEnum
from typing import Final, Never


class NetworkFailureCode(StrEnum):
    NEXT_CHUNK_ABORT_PRIOR_DOCUMENT = "next_chunk_abort_prior_document"
    NEXT_CHUNK_ABORT_CURRENT_DOCUMENT = "next_chunk_abort_current_document"
    NEXT_CHUNK_ABORT_UNOBSERVED = "next_chunk_abort_unobserved"
    API_REQUEST_ABORT = "api_request_abort"
    API_REQUEST_FAILURE = "api_request_failure"
    API_RESPONSE_FAILURE = "api_response_failure"
    OTHER_REQUEST_ABORT = "other_request_abort"
    OTHER_REQUEST_FAILURE = "other_request_failure"
    OTHER_RESPONSE_FAILURE = "other_response_failure"


NETWORK_FAILURE_ANNOTATION_TYPE: Final = "moldy.network-failure.v1"
NETWORK_FAILURE_CODES: Final[frozenset[NetworkFailureCode]] = frozenset(
    NetworkFailureCode
)
NETWORK_FAILURE_CODE_BY_VALUE: Final[dict[str, NetworkFailureCode]] = {
    code.value: code for code in NetworkFailureCode
}


class NetworkFailureCodeError(ValueError):
    """Stable rejection that never reflects an untrusted annotation."""


def _fail() -> Never:
    raise NetworkFailureCodeError("invalid_network_failure_codes")


def _network_failure_code(value: object) -> NetworkFailureCode | None:
    return NETWORK_FAILURE_CODE_BY_VALUE.get(value) if isinstance(value, str) else None


def extract_network_failure_codes(result: object) -> tuple[NetworkFailureCode, ...]:
    """Project exact reserved result annotations onto a bounded enum tuple."""
    if not isinstance(result, dict):
        return ()
    annotations = result.get("annotations")
    if not isinstance(annotations, list):
        return ()
    codes: set[NetworkFailureCode] = set()
    for annotation in annotations:
        if not isinstance(annotation, dict) or set(annotation) != {
            "type",
            "description",
        }:
            continue
        if annotation.get("type") != NETWORK_FAILURE_ANNOTATION_TYPE:
            continue
        code = _network_failure_code(annotation.get("description"))
        if code is not None:
            codes.add(code)
        if len(codes) == len(NETWORK_FAILURE_CODES):
            break
    return tuple(sorted(codes))


def parse_network_failure_codes(value: object) -> tuple[NetworkFailureCode, ...]:
    """Parse the persisted optional code list and reject all non-canonical shapes."""
    if not isinstance(value, list) or not 1 <= len(value) <= len(NETWORK_FAILURE_CODES):
        _fail()
    parsed = tuple(
        code for item in value if (code := _network_failure_code(item)) is not None
    )
    if len(parsed) != len(value):
        _fail()
    if value != sorted(set(value)):
        _fail()
    return parsed
