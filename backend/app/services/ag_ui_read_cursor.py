"""Authenticated positions in one run's committed, projected event stream."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from typing import Literal, cast

from app.security.key_provider import get_keys

CursorKind = Literal["head", "event", "terminal"]
_NUMBER = r"(?:0|[1-9][0-9]{0,18})"
_PATTERN = re.compile(
    rf"g1\.(head|event|terminal)\.({_NUMBER})\.({_NUMBER})\.([0-9a-f]{{32}})\.([0-9a-f]{{32}})"
)
_MAX_POSITION = 2**63 - 1


class InvalidReadCursor(ValueError):
    """The caller must recover from its authorized snapshot."""


@dataclass(frozen=True, slots=True)
class CursorScope:
    thread_id: str
    public_run_id: str
    physical_run_id: str | None
    projection_version: str
    branch_key: str | None
    visibility_key: str = ""
    fingerprint: bytes = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        fields = (
            self.thread_id,
            self.public_run_id,
            self.physical_run_id,
            self.projection_version,
            self.branch_key,
            self.visibility_key,
        )
        encoded = json.dumps(fields, separators=(",", ":")).encode("ascii")
        object.__setattr__(self, "fingerprint", hashlib.sha256(encoded).digest())


@dataclass(frozen=True, slots=True)
class CursorPosition:
    kind: CursorKind
    ordinal: int
    frame: int
    source_digest: str
    scope_fingerprint: bytes = field(repr=False)


def _check_position(kind: CursorKind, ordinal: int, frame: int) -> None:
    if (
        type(ordinal) is not int
        or type(frame) is not int
        or not 0 <= ordinal <= _MAX_POSITION
        or not 0 <= frame <= _MAX_POSITION
        or kind not in ("head", "event", "terminal")
        or (kind == "head" and (ordinal, frame) != (0, 0))
        or (kind != "head" and ordinal == 0)
    ):
        raise InvalidReadCursor("invalid stream cursor")


def _mac(key: bytes, data: bytes) -> str:
    return hmac.digest(key, data, "sha256")[:16].hex()


def _source_data(
    scope_fingerprint: bytes, kind: CursorKind, source_id: str, generation: str | None
) -> bytes:
    # A real run may be accepted before its first journal row exists.
    generation = None if kind == "head" else generation
    source = json.dumps((kind, generation, source_id), separators=(",", ":")).encode("ascii")
    return b"moldy:ag-ui:cursor-source:v1\0" + scope_fingerprint + source


def _position_data(scope: CursorScope, body: str) -> bytes:
    return b"moldy:ag-ui:cursor-position:v1\0" + scope.fingerprint + body.encode("ascii")


def frame_cursor(
    scope: CursorScope,
    *,
    ordinal: int,
    frame: int,
    source_id: str,
    generation: str | None,
    kind: CursorKind = "event",
) -> str:
    """Frame is zero-based within a durable source ordinal, independent of SDK sequence numbers."""
    _check_position(kind, ordinal, frame)
    key = get_keys()[0].raw
    digest = _mac(key, _source_data(scope.fingerprint, kind, source_id, generation))
    body = f"g1.{kind}.{ordinal}.{frame}.{digest}"
    return f"{body}.{_mac(key, _position_data(scope, body))}"


def parse_cursor(token: str, scope: CursorScope) -> CursorPosition:
    """Reject invalid positions before using their ordinals to read the journal."""
    match = _PATTERN.fullmatch(token)
    if not match:
        raise InvalidReadCursor("invalid stream cursor")
    kind = cast(CursorKind, match[1])
    ordinal, frame = int(match[2]), int(match[3])
    _check_position(kind, ordinal, frame)
    body = _position_data(scope, token.rsplit(".", 1)[0])
    if not any(hmac.compare_digest(_mac(key.raw, body), match[5]) for key in get_keys()):
        raise InvalidReadCursor("invalid stream cursor")
    return CursorPosition(kind, ordinal, frame, match[4], scope.fingerprint)


def matches_source(position: CursorPosition, *, source_id: str, generation: str | None) -> bool:
    """Verify the cursor against the source still stored at that ordinal."""
    data = _source_data(position.scope_fingerprint, position.kind, source_id, generation)
    return any(
        hmac.compare_digest(_mac(key.raw, data), position.source_digest) for key in get_keys()
    )
