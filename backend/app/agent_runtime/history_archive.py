"""Immutable archives of the messages evicted by a chat compaction.

The manifest names ordered content-addressed chunks and the previous checkpoint's
manifest. Publishing it last makes a partially written batch invisible. All files
live below the conversation's existing history deletion fence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from uuid import UUID

from langchain_core.messages import AnyMessage, BaseMessage, messages_from_dict
from pydantic import BaseModel, ConfigDict, Field

from app.agent_runtime.history_archive_render import render_archive_message
from app.agent_runtime.offload_storage_fd import safe_dir
from app.agent_runtime.offload_storage_io import atomic_write_new_or_equal, read_scoped_regular_file
from app.agent_runtime.offload_storage_lock import conversation_mutation_lock
from app.agent_runtime.offload_storage_types import (
    ARCHIVE_FILE,
    PHYSICAL_ROOT,
    SESSION_FILE,
    OffloadMutationScope,
    OffloadSecurityError,
    parse_actor,
    token,
)

CHUNK_BYTES = 256 * 1024


class _Chunk(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    length: int = Field(gt=0, le=CHUNK_BYTES)


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    version: Literal[1] = 1
    encoding: Literal["messages-jsonl", "legacy-markdown"]
    checkpoint: str
    previous: str | None
    length: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    chunks: list[_Chunk]


def _message_bytes(messages: Sequence[BaseMessage]) -> Iterator[bytes]:
    for message in messages:
        serialized = message.model_dump_json()
        for start in range(0, len(serialized), CHUNK_BYTES // 4):
            yield serialized[start : start + CHUNK_BYTES // 4].encode("utf-8")
        yield b"\n"


def _chunks(pieces: Iterable[bytes]) -> Iterator[bytes]:
    pending = bytearray()
    for piece in pieces:
        remaining = memoryview(piece)
        while remaining:
            count = min(CHUNK_BYTES - len(pending), len(remaining))
            pending.extend(remaining[:count])
            remaining = remaining[count:]
            if len(pending) == CHUNK_BYTES:
                yield bytes(pending)
                pending.clear()
    if pending:
        yield bytes(pending)


def iter_json_lines(pieces: Iterable[bytes]) -> Iterator[bytes]:
    pending = bytearray()
    for piece in pieces:
        parts = piece.split(b"\n")
        pending.extend(parts[0])
        for part in parts[1:]:
            yield bytes(pending)
            pending.clear()
            pending.extend(part)
    if pending:
        raise OffloadSecurityError(reason="archive message is incomplete")


@dataclass(frozen=True, slots=True)
class HistoryArchive:
    scope: OffloadMutationScope
    actor: str

    @property
    def directory(self) -> Path:
        return self.scope.internal_root.joinpath(
            "history", self.scope.owner, self.scope.conversation, "archives-v1", self.actor
        )

    def _manifest(self, leaf: str) -> _Manifest:
        if not ARCHIVE_FILE.fullmatch(leaf):
            raise OffloadSecurityError(reason="invalid history archive reference")
        raw = read_scoped_regular_file(self.directory, ("manifests", leaf))
        if leaf != _manifest_leaf(raw):
            raise OffloadSecurityError(reason="archive manifest digest mismatch")
        manifest = _Manifest.model_validate_json(raw)
        if manifest.previous and not ARCHIVE_FILE.fullmatch(manifest.previous):
            raise OffloadSecurityError(reason="invalid previous archive reference")
        if sum(chunk.length for chunk in manifest.chunks) != manifest.length:
            raise OffloadSecurityError(reason="invalid archive length")
        return manifest

    def _store(
        self,
        *,
        encoding: Literal["messages-jsonl", "legacy-markdown"],
        checkpoint: str,
        previous: str | None,
        pieces: Iterable[bytes],
    ) -> str:
        blobs = safe_dir(self.directory, ("chunks",))
        manifests = safe_dir(self.directory, ("manifests",))
        parts: list[_Chunk] = []
        total = hashlib.sha256()
        length = 0
        for data in _chunks(pieces):
            digest = hashlib.sha256(data).hexdigest()
            atomic_write_new_or_equal(blobs / digest, data)
            # A fresh manifest can only reference bytes verified on this storage.
            saved = read_scoped_regular_file(blobs, (digest,), max_bytes=len(data))
            if len(saved) != len(data) or hashlib.sha256(saved).hexdigest() != digest:
                raise OffloadSecurityError(reason="archive chunk verification failed")
            parts.append(_Chunk(sha256=digest, length=len(data)))
            total.update(data)
            length += len(data)
        manifest = _Manifest(
            encoding=encoding,
            checkpoint=checkpoint,
            previous=previous,
            length=length,
            sha256=total.hexdigest(),
            chunks=parts,
        )
        encoded = manifest.model_dump_json().encode("utf-8")
        leaf = _manifest_leaf(encoded)
        atomic_write_new_or_equal(manifests / leaf, encoded)
        return leaf

    def save(
        self,
        checkpoint_id: str,
        messages: Sequence[BaseMessage],
        *,
        previous: str | None,
        read_legacy: Callable[[str], bytes],
    ) -> str:
        if not checkpoint_id or not messages:
            raise ValueError("an archive requires a checkpoint and evicted messages")
        checkpoint = token("history-checkpoint", checkpoint_id)
        with conversation_mutation_lock(self.scope):
            if previous:
                if SESSION_FILE.fullmatch(previous):
                    # Freeze the single-file predecessor, which older runtimes append to.
                    previous = self._store(
                        encoding="legacy-markdown",
                        checkpoint=checkpoint,
                        previous=None,
                        pieces=(read_legacy(previous),),
                    )
                else:
                    self._manifest(previous)
            return self._store(
                encoding="messages-jsonl",
                checkpoint=checkpoint,
                previous=previous,
                pieces=_message_bytes(messages),
            )

    def _history(self, leaf: str) -> Iterator[_Manifest]:
        reverse: list[str] = []
        seen: set[str] = set()
        current: str | None = leaf
        while current is not None:
            if current in seen:
                raise OffloadSecurityError(reason="cyclic history archive")
            seen.add(current)
            reverse.append(current)
            current = self._manifest(current).previous
        for item in reversed(reverse):
            yield self._manifest(item)

    def _bytes(self, manifest: _Manifest) -> Iterator[bytes]:
        combined = hashlib.sha256()
        for chunk in manifest.chunks:
            data = read_scoped_regular_file(
                self.directory, ("chunks", chunk.sha256), max_bytes=chunk.length
            )
            if len(data) != chunk.length or hashlib.sha256(data).hexdigest() != chunk.sha256:
                raise OffloadSecurityError(reason="archive chunk digest mismatch")
            combined.update(data)
            yield data
        if combined.hexdigest() != manifest.sha256:
            raise OffloadSecurityError(reason="archive payload digest mismatch")

    def source_checkpoint_matches(self, leaf: str, checkpoint_id: str) -> bool:
        return self._manifest(leaf).checkpoint == token("history-checkpoint", checkpoint_id)

    def raw_messages(self, leaf: str) -> Iterator[bytes]:
        """Yield verified original JSON Lines, excluding a frozen legacy Markdown prefix."""
        for manifest in self._history(leaf):
            if manifest.encoding == "messages-jsonl":
                yield from self._bytes(manifest)

    def rendered(
        self, leaf: str, *, history_prefix: str, read_media: Callable[[str], bytes]
    ) -> Iterator[bytes]:
        """Render the model-facing history using the Deep Agents XML formatter."""
        for manifest in self._history(leaf):
            if manifest.encoding == "legacy-markdown":
                yield from self._bytes(manifest)
                yield b"\n"
            else:
                for line in iter_json_lines(self._bytes(manifest)):
                    data = json.loads(line)
                    messages = messages_from_dict([{"type": data["type"], "data": data}])
                    yield render_archive_message(
                        cast("AnyMessage", messages[0]),
                        history_prefix=history_prefix,
                        read_media=read_media,
                    )
                    yield b"\n"


def _manifest_leaf(encoded: bytes) -> str:
    return f"archive_{hashlib.sha256(encoded).hexdigest()[:32]}.md"


def _history_archive_reader(
    data_dir: Path, *, owner_id: str, conversation_id: str, actor_id: UUID | str
) -> HistoryArchive:
    scope = OffloadMutationScope(
        data_dir.resolve() / PHYSICAL_ROOT / "offload",
        token("owner", owner_id),
        token("conversation", conversation_id),
    )
    return HistoryArchive(scope, token("actor", parse_actor(actor_id)))


def iter_history_archive_bytes(
    data_dir: Path, *, owner_id: str, conversation_id: str, actor_id: UUID | str, leaf: str
) -> Iterator[bytes]:
    """Open an exact actor's archive after the caller authorizes its checkpoint."""
    archive = _history_archive_reader(
        data_dir, owner_id=owner_id, conversation_id=conversation_id, actor_id=actor_id
    )
    return archive.raw_messages(leaf)


def history_archive_checkpoint_matches(
    data_dir: Path,
    *,
    owner_id: str,
    conversation_id: str,
    actor_id: UUID | str,
    leaf: str,
    checkpoint_id: str,
) -> bool:
    """Verify an archive was written by this particular source checkpoint."""
    archive = _history_archive_reader(
        data_dir, owner_id=owner_id, conversation_id=conversation_id, actor_id=actor_id
    )
    return archive.source_checkpoint_matches(leaf, checkpoint_id)
