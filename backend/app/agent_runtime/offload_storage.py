"""Owner-scoped storage facade for Deep Agents history and result offloads."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import assert_never
from uuid import UUID

from deepagents.backends import CompositeBackend
from deepagents.backends.protocol import BackendProtocol, GlobResult, GrepResult, LsResult
from langchain_core.messages import BaseMessage, get_buffer_string

from app.agent_runtime.history_archive import HistoryArchive
from app.agent_runtime.offload_storage_backends import (
    HiddenFilesystemBackend,
    HistoryBackend,
    RollbackHistoryBackend,
    SpillBackend,
    parts,
    safe_dir,
)
from app.agent_runtime.offload_storage_cleanup import conversation_mutation_lock
from app.agent_runtime.offload_storage_fd import ensure_private_root
from app.agent_runtime.offload_storage_io import atomic_write_new_or_equal, read_scoped_regular_file
from app.agent_runtime.offload_storage_lifecycle import (
    delete_conversation_offloads,  # noqa: F401 -- compatibility export
    gc_history_conversation,
    gc_spill_conversation,
    gc_spill_run,
)
from app.agent_runtime.offload_storage_types import (
    ARCHIVE_FILE,
    GENERAL_PURPOSE_ACTOR,  # noqa: F401 -- compatibility export
    PHYSICAL_ROOT,
    ROLLBACK_SESSION_FILE,
    SAFE_LEGACY_NAME,
    SESSION_FILE,
    VIRTUAL_ROOT,
    LegacyHistoryKind,
    MigrationReceipt,
    OffloadCleanupReceipt,  # noqa: F401 -- compatibility export
    OffloadGcReceipt,
    OffloadGcScope,
    OffloadIdentity,
    OffloadKind,
    OffloadMutationScope,
    OffloadProjection,
    OffloadSecurityError,
    logical_offload_id,
    parse_actor,
    token,
)


class ScopedOffloadBackend(CompositeBackend):
    """Deep Agents backend exposing only exact, owner-scoped offload references."""

    def __init__(
        self,
        data_dir: Path,
        identity: OffloadIdentity,
        actor_id: UUID | str,
        *,
        archive_owner_id: str | None = None,
    ) -> None:
        actor = parse_actor(actor_id)
        self._data_dir = data_dir.resolve()
        self._identity = identity
        self._owner = token("owner", identity.owner_id)
        self._conversation = token("conversation", identity.conversation_id)
        self._run = token("run", identity.run_id)
        self._actor = token("actor", actor)
        private_root = ensure_private_root(self._data_dir / PHYSICAL_ROOT)
        internal = private_root / "offload"
        self._mutation_scope = OffloadMutationScope(
            internal_root=internal,
            owner=self._owner,
            conversation=self._conversation,
        )
        self._internal_root = internal
        self._gc_scope = OffloadGcScope(
            internal_root=internal,
            owner=self._owner,
            conversation=self._conversation,
            run=self._run,
        )
        with conversation_mutation_lock(self._mutation_scope):
            history_root = safe_dir(internal, ("history", self._owner, self._conversation))
            self._spill_conversation_root = safe_dir(
                internal, ("spill", self._owner, self._conversation)
            )
            self._current_run_root = safe_dir(self._spill_conversation_root, (self._run,))
            self._current_spill_root = safe_dir(self._current_run_root, (self._actor,))
        artifacts_root = f"{VIRTUAL_ROOT}/{self._owner}/{self._conversation}/{self._actor}"
        self._archive_owner_id = archive_owner_id
        archive_scope = OffloadMutationScope(
            internal, token("owner", archive_owner_id or identity.owner_id), self._conversation
        )
        self._archive = HistoryArchive(archive_scope, self._actor)
        self._rollback_history = RollbackHistoryBackend(archive_scope, self._actor, self._owner)
        routes = {
            f"{artifacts_root}/conversation_history/": HistoryBackend(
                history_root,
                archive=self._archive,
                history_prefix=f"{artifacts_root}/conversation_history",
                mutation_scope=self._mutation_scope,
                virtual_mode=True,
            ),
            f"{artifacts_root}/large_tool_results/": SpillBackend(
                self._current_spill_root,
                self._spill_conversation_root,
                self._actor,
                self._mutation_scope,
            ),
        }
        super().__init__(
            default=HiddenFilesystemBackend(self._data_dir, virtual_mode=True),
            routes=routes,
            artifacts_root=artifacts_root,
        )

    def for_actor(self, actor_id: UUID | str) -> ScopedOffloadBackend:
        """Build an actor sibling while retaining the trusted request scope."""
        return ScopedOffloadBackend(
            self._data_dir, self._identity, actor_id, archive_owner_id=self._archive_owner_id
        )

    def _history_leaf(self, path: str) -> str:
        prefix = f"{self.artifacts_root}/conversation_history/"
        leaf = path.removeprefix(prefix)
        if path == leaf or not (ARCHIVE_FILE.fullmatch(leaf) or SESSION_FILE.fullmatch(leaf)):
            raise OffloadSecurityError(reason="history reference is outside this actor")
        return leaf

    def _get_backend_and_key(self, key: str) -> tuple[BackendProtocol, str]:
        backend, stripped = super()._get_backend_and_key(key)
        if isinstance(backend, HistoryBackend) and ROLLBACK_SESSION_FILE.fullmatch(
            stripped.removeprefix("/")
        ):
            return self._rollback_history, stripped
        return backend, stripped

    def archive_checkpoint_to_legacy(
        self, archive_file_path: str, session_id: str, evicted: Sequence[BaseMessage]
    ) -> str:
        """Resume a chunked checkpoint in an immutable-at-creation Markdown snapshot."""
        leaf = self._history_leaf(archive_file_path)
        filename = f"{session_id}.md"
        if not ARCHIVE_FILE.fullmatch(leaf) or not ROLLBACK_SESSION_FILE.fullmatch(filename):
            raise OffloadSecurityError(reason="invalid archive rollback reference")
        prefix = f"{self.artifacts_root}/conversation_history/"
        history = self.routes[prefix]
        if not isinstance(history, HistoryBackend):
            raise OffloadSecurityError(reason="durable history route is unavailable")

        def markdown() -> Iterator[bytes]:
            yield from history.archive_markdown(leaf)
            yield b"\n## Summarized\n\n"
            for message in evicted:
                yield get_buffer_string([message], format="xml").encode("utf-8")
                yield b"\n"
            yield b"\n"

        self._rollback_history.seed(filename, markdown())
        return f"{prefix}{filename}"

    def archive_compaction(
        self,
        checkpoint_id: str,
        messages: Sequence[BaseMessage],
        *,
        previous_file_path: str | None = None,
    ) -> str:
        """Archive the trusted evicted batch before publishing its summary."""
        history = self.routes[f"{self.artifacts_root}/conversation_history/"]
        if not isinstance(history, HistoryBackend):
            raise OffloadSecurityError(reason="durable history route is unavailable")

        def read_legacy(name: str) -> bytes:
            if ROLLBACK_SESSION_FILE.fullmatch(name):
                return self._rollback_history.read_legacy(name)
            return history.read_legacy(name)

        leaf = self._archive.save(
            checkpoint_id,
            messages,
            previous=self._history_leaf(previous_file_path) if previous_file_path else None,
            read_legacy=read_legacy,
        )
        return f"{self.artifacts_root}/conversation_history/{leaf}"

    def iter_archive_bytes(self, file_path: str) -> Iterator[bytes]:
        """Read a verified immutable archive within this actor's exact namespace."""
        return self._archive.raw_messages(self._history_leaf(file_path))

    def ls(self, path: str) -> LsResult:
        if path == "/":
            return self.default.ls(path)
        if path == self.artifacts_root or path.startswith(f"{self.artifacts_root}/"):
            return LsResult(error="internal offload directories cannot be enumerated")
        return self.default.ls(path)

    async def als(self, path: str) -> LsResult:
        return self.ls(path)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        if path is None or path == "/":
            return self.default.glob(pattern, path)
        if path == self.artifacts_root or path.startswith(f"{self.artifacts_root}/"):
            return GlobResult(error="internal offload directories cannot be enumerated")
        return self.default.glob(pattern, path)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self.glob(pattern, path)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        if path is None or path == "/":
            return self.default.grep(pattern, path, glob, max_count=max_count)
        if path == self.artifacts_root or path.startswith(f"{self.artifacts_root}/"):
            return GrepResult(error="internal offload directories cannot be searched")
        return self.default.grep(pattern, path, glob, max_count=max_count)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return self.grep(pattern, path, glob, max_count=max_count)

    def project_reference(self, path: str) -> OffloadProjection | None:
        scoped = path.removeprefix(self.artifacts_root)
        candidate = scoped if scoped != path else path
        if candidate.startswith("/conversation_history/"):
            leaf = candidate.removeprefix("/conversation_history/")
            if not (SESSION_FILE.fullmatch(leaf) or ARCHIVE_FILE.fullmatch(leaf)):
                return None
            reference = f"{self.artifacts_root}/conversation_history/{leaf}"
            return OffloadProjection(
                kind=OffloadKind.HISTORY,
                logical_id=logical_offload_id(OffloadKind.HISTORY, reference),
            )
        if candidate.startswith("/large_tool_results/"):
            leaf = candidate.removeprefix("/large_tool_results/")
            try:
                leaf_parts = parts(leaf)
            except PermissionError:
                return None
            if len(leaf_parts) != 1:
                return None
            reference = f"{self.artifacts_root}/large_tool_results/{leaf}"
            return OffloadProjection(
                kind=OffloadKind.SPILL,
                logical_id=logical_offload_id(OffloadKind.SPILL, reference),
            )
        return None

    def migrate_legacy_history(
        self,
        source_kind: LegacyHistoryKind,
        *,
        source_filename: str,
    ) -> MigrationReceipt:
        """Copy one explicitly owned legacy source and retain the original."""
        legacy_root = self._data_dir / "conversation_history"
        if legacy_root.is_symlink():
            raise OffloadSecurityError(reason="legacy history root cannot be a symlink")
        match source_kind:
            case LegacyHistoryKind.SESSION:
                verified = source_filename in self._identity.verified_legacy_sessions
                if not verified or SESSION_FILE.fullmatch(source_filename) is None:
                    raise OffloadSecurityError(reason="legacy session source is not verified")
                source_name = source_filename
                destination_name = source_filename
            case LegacyHistoryKind.CONVERSATION:
                trusted = self._identity.conversation_id
                owned = (
                    SAFE_LEGACY_NAME.fullmatch(trusted) is not None
                    and source_filename == f"{trusted}.md"
                )
                if not owned:
                    raise OffloadSecurityError(reason="legacy conversation source is not owned")
                source_name = source_filename
                destination_name = f"session_{token('legacy-conversation', trusted, length=32)}.md"
            case unreachable:
                assert_never(unreachable)
        try:
            content = read_scoped_regular_file(
                self._data_dir, ("conversation_history", source_name)
            )
        except OffloadSecurityError as error:
            raise OffloadSecurityError(reason=f"legacy history source rejected: {error}") from error
        digest = hashlib.sha256(content).hexdigest()
        virtual_path = f"{self.artifacts_root}/conversation_history/{destination_name}"
        history = self.routes[f"{self.artifacts_root}/conversation_history/"]
        if not isinstance(history, HistoryBackend):
            raise OffloadSecurityError(reason="durable history route is unavailable")
        reserved = ROLLBACK_SESSION_FILE.fullmatch(destination_name) is not None
        lock = (
            self._rollback_history.import_or_rollback_lock()
            if reserved
            else conversation_mutation_lock(self._mutation_scope)
        )
        with lock:
            if reserved and self._rollback_history.published(destination_name):
                raise OffloadSecurityError(
                    reason="import conflicts with published rollback history"
                )
            safe_dir(
                self._internal_root,
                ("history", self._owner, self._conversation),
            )
            destination = history._resolve_path(f"/{destination_name}")  # noqa: SLF001
            atomic_write_new_or_equal(destination, content)
            projection = self.project_reference(virtual_path)
            if projection is None:
                raise OffloadSecurityError(reason="migrated history reference is invalid")
            receipt = MigrationReceipt(
                source_kind=source_kind,
                source_logical_id=token(
                    "legacy-source", f"{source_kind.value}:{source_name}", length=24
                ),
                virtual_path=virtual_path,
                logical_id=projection.logical_id,
                content_sha256=digest,
                size=len(content),
            )
            receipt_dir = safe_dir(destination.parent, ("receipts",))
            receipt_name = token("migration-receipt", f"{source_kind.value}:{source_name}")
            atomic_write_new_or_equal(
                receipt_dir / f"{receipt_name}.json",
                json.dumps(asdict(receipt), sort_keys=True).encode(),
            )
        return receipt

    def gc_spill_run(self) -> OffloadGcReceipt:
        return gc_spill_run(self._gc_scope)

    def gc_spill_conversation(self, *, protected_run_ids: frozenset[str]) -> OffloadGcReceipt:
        return gc_spill_conversation(self._gc_scope, protected_run_ids)

    def gc_history_conversation(self) -> OffloadGcReceipt:
        route = self.routes[f"{self.artifacts_root}/conversation_history/"]
        if not isinstance(route, HistoryBackend):
            raise OffloadSecurityError(reason="durable history route is unavailable")
        return gc_history_conversation(self._gc_scope)


@dataclass(frozen=True, slots=True)
class ScopedOffloadStorage:
    """Factory retaining trusted request identity while selecting an actor."""

    data_dir: Path
    identity: OffloadIdentity
    archive_owner_id: str | None = None

    def for_actor(self, actor_id: UUID | str) -> ScopedOffloadBackend:
        return ScopedOffloadBackend(
            self.data_dir, self.identity, actor_id, archive_owner_id=self.archive_owner_id
        )
