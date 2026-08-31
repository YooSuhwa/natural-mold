"""Typed values shared by the scoped Deep Agents offload boundary."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final
from uuid import UUID

GENERAL_PURPOSE_ACTOR: Final = "general-purpose"
VIRTUAL_ROOT: Final = "/.moldy-offload"
PHYSICAL_ROOT: Final = ".moldy-internal"
SESSION_FILE: Final = re.compile(r"session_[0-9a-f]{32}\.md\Z")
MEDIA_FILE: Final = re.compile(r"[0-9a-f]{16}\.[a-z0-9]{1,10}\Z")
SAFE_LEGACY_NAME: Final = re.compile(r"[A-Za-z0-9_-]+\Z")


@dataclass(slots=True)
class OffloadSecurityError(ValueError):
    """Reject an offload identity or path that crosses a trust boundary."""

    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class OffloadIdentity:
    """Trusted request identity used to derive opaque storage namespaces."""

    owner_id: str
    conversation_id: str
    run_id: str
    verified_legacy_sessions: frozenset[str] = frozenset()


class LegacyHistoryKind(StrEnum):
    SESSION = "session"
    CONVERSATION = "conversation"


class OffloadKind(StrEnum):
    HISTORY = "history"
    SPILL = "spill"


@dataclass(frozen=True, slots=True)
class OffloadProjection:
    kind: OffloadKind
    logical_id: str


@dataclass(frozen=True, slots=True)
class MigrationReceipt:
    source_kind: LegacyHistoryKind
    source_logical_id: str
    virtual_path: str
    logical_id: str
    content_sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class OffloadGcReceipt:
    removed_runs: int = 0
    removed_files: int = 0


@dataclass(frozen=True, slots=True)
class OffloadGcScope:
    internal_root: Path
    owner: str
    conversation: str
    run: str

    @property
    def mutation_scope(self) -> OffloadMutationScope:
        return OffloadMutationScope(
            internal_root=self.internal_root,
            owner=self.owner,
            conversation=self.conversation,
        )


@dataclass(frozen=True, slots=True)
class OffloadMutationScope:
    """Opaque owner/conversation identity for serialized physical mutations."""

    internal_root: Path
    owner: str
    conversation: str


@dataclass(frozen=True, slots=True)
class OffloadCleanupReceipt:
    history_files: int = 0
    spill_files: int = 0


def logical_offload_id(kind: OffloadKind, scoped_reference: str) -> str:
    """Project one validated scoped virtual reference to an opaque protocol ID."""
    digest = token(f"{kind.value}-logical-reference", scoped_reference, length=24)
    return f"{kind.value}_{digest}"


def token(domain: str, raw: str, *, length: int = 32) -> str:
    digest = hashlib.sha256(f"moldy-offload:{domain}\0{raw}".encode()).hexdigest()
    return digest[:length]


def parse_actor(actor_id: UUID | str) -> str:
    if actor_id == GENERAL_PURPOSE_ACTOR:
        return GENERAL_PURPOSE_ACTOR
    if isinstance(actor_id, UUID):
        return str(actor_id)
    try:
        parsed = UUID(actor_id)
    except ValueError as error:
        raise OffloadSecurityError(reason="actor identity must be a full UUID") from error
    if str(parsed) != actor_id:
        raise OffloadSecurityError(reason="actor identity must use canonical UUID form")
    return actor_id
