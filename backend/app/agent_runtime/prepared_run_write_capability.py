"""Exact same-run write capability for post-marker persistence (parent Task 10, I14).

A prepared run may persist only its own events/chunks, metrics, usage, trace,
branch leaf, listed attachments, artifacts under its output root, checkpoint
descendants and run/input/envelope terminal fields. Every write names the exact
bound identifiers; a foreign ID or path escape is denied before any SQL or
filesystem access. Runner/tools never receive a generic session.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

WRITE_DENIED = "run_write_capability_denied"

EnvelopeKind = Literal["api", "trigger"]


class RunWriteCapabilityDeniedError(PermissionError):
    code = WRITE_DENIED

    def __init__(self, reason: str) -> None:
        super().__init__(f"{WRITE_DENIED}:{reason}")
        self.reason = reason


@dataclass(frozen=True, slots=True)
class RunWriteBinding:
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    org_id: uuid.UUID
    output_root: Path
    input_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    attachment_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    api_run_id: uuid.UUID | None = None
    trigger_run_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class PreparedRunWriteCapability:
    binding: RunWriteBinding

    def require_run(self, run_id: uuid.UUID | str) -> uuid.UUID:
        return self._exact("run", run_id, {self.binding.run_id})

    def require_conversation(self, conversation_id: uuid.UUID | str) -> uuid.UUID:
        return self._exact("conversation", conversation_id, {self.binding.conversation_id})

    def require_input(self, input_id: uuid.UUID | str) -> uuid.UUID:
        return self._exact("input", input_id, self.binding.input_ids)

    def require_attachment(self, attachment_id: uuid.UUID | str) -> uuid.UUID:
        return self._exact("attachment", attachment_id, self.binding.attachment_ids)

    def require_envelope(self, kind: EnvelopeKind, envelope_id: uuid.UUID | str) -> uuid.UUID:
        if kind not in ("api", "trigger"):
            raise RunWriteCapabilityDeniedError("envelope_kind")
        bound = self.binding.api_run_id if kind == "api" else self.binding.trigger_run_id
        return self._exact(f"{kind}_envelope", envelope_id, {bound} if bound else set())

    def output_path(self, relative: str) -> Path:
        """Resolve ``relative`` under the run output root or deny (no escape, no absolute)."""

        pure = PurePosixPath(relative)
        if not relative or pure.is_absolute() or ".." in pure.parts or "\x00" in relative:
            raise RunWriteCapabilityDeniedError("path")
        root = self.binding.output_root.resolve()
        candidate = (root / Path(*pure.parts)).resolve()
        # The root itself is not a write target; resolution happens here once,
        # so writers must open the returned path without following new links.
        if root not in candidate.parents:
            raise RunWriteCapabilityDeniedError("path")
        return candidate

    @staticmethod
    def _exact(
        kind: str, value: uuid.UUID | str, allowed: set[uuid.UUID] | frozenset[uuid.UUID]
    ) -> uuid.UUID:
        try:
            identifier = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        except ValueError as error:
            raise RunWriteCapabilityDeniedError(kind) from error
        if identifier not in allowed:
            raise RunWriteCapabilityDeniedError(kind)
        return identifier
