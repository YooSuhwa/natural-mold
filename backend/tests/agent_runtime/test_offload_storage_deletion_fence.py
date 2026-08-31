from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

import pytest

from app.agent_runtime import (
    offload_storage_cleanup,
    offload_storage_lifecycle,
    offload_storage_lock,
)
from app.agent_runtime.offload_storage import (
    OffloadIdentity,
    OffloadSecurityError,
    ScopedOffloadBackend,
    ScopedOffloadStorage,
    delete_conversation_offloads,
)
from app.agent_runtime.offload_storage_types import OffloadMutationScope

ACTOR = UUID("11111111-1111-4111-8111-111111111111")


def _backend(
    data_dir: Path,
    *,
    run: str = "run-a",
    verified: frozenset[str] = frozenset(),
) -> ScopedOffloadBackend:
    return ScopedOffloadStorage(
        data_dir=data_dir,
        identity=OffloadIdentity("user-a", "conversation-a", run, verified),
    ).for_actor(ACTOR)


def _spill_path(backend: ScopedOffloadBackend, name: str = "tool") -> str:
    return f"{backend.artifacts_root}/large_tool_results/{name}"


def _write_spill(backend: ScopedOffloadBackend, name: str, content: str = "secret") -> None:
    assert backend.write(_spill_path(backend, name), content).error is None


def test_gc_rejects_target_replaced_before_lock_without_deleting_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    backend = _backend(tmp_path)
    _write_spill(backend, "tool", "original")
    target = backend._current_run_root
    original_lock = offload_storage_cleanup.conversation_mutation_lock

    @contextmanager
    def swap_before_lock(scope: OffloadMutationScope) -> Iterator[None]:
        target.rename(target.with_name(f"{target.name}.moved"))
        target.mkdir()
        (target / "replacement").write_text("keep")
        with original_lock(scope):
            yield

    monkeypatch.setattr(offload_storage_lifecycle, "conversation_mutation_lock", swap_before_lock)

    # When / Then
    with pytest.raises(OffloadSecurityError, match="changed"):
        backend.gc_spill_run()
    assert (target / "replacement").read_text() == "keep"


def test_gc_rejects_target_replaced_after_validation_without_deleting_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    backend = _backend(tmp_path)
    _write_spill(backend, "tool", "original")
    target = backend._current_run_root
    original_remove = offload_storage_cleanup._remove_directory_contents
    swapped = False

    def swap_after_validation(descriptor: int) -> int:
        nonlocal swapped
        if not swapped:
            swapped = True
            target.rename(target.with_name(f"{target.name}.moved"))
            target.mkdir()
            (target / "replacement").write_text("keep")
        return original_remove(descriptor)

    monkeypatch.setattr(
        offload_storage_cleanup, "_remove_directory_contents", swap_after_validation
    )

    # When / Then
    with pytest.raises(OffloadSecurityError, match="changed"):
        backend.gc_spill_run()
    assert (target / "replacement").read_text() == "keep"


def test_gc_rejects_file_replaced_before_unlink_without_deleting_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    backend = _backend(tmp_path)
    _write_spill(backend, "tool", "original")
    physical = next(backend._current_spill_root.iterdir())
    original_unlink = offload_storage_cleanup._unlink_regular

    def swap_before_unlink(
        parent: int,
        name: str,
        expected: offload_storage_cleanup._DirectoryIdentity,
    ) -> None:
        physical.rename(physical.with_name(f"{physical.name}.moved"))
        physical.write_text("replacement")
        original_unlink(parent, name, expected)

    monkeypatch.setattr(offload_storage_cleanup, "_unlink_regular", swap_before_unlink)

    # When / Then
    with pytest.raises(OffloadSecurityError, match="changed"):
        backend.gc_spill_run()
    assert physical.read_text() == "replacement"


def test_delete_fences_and_removes_conversation_created_before_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    original_lock = offload_storage_lifecycle.conversation_deletion_lock
    created: list[ScopedOffloadBackend] = []

    @contextmanager
    def create_before_lock(scope: OffloadMutationScope) -> Iterator[None]:
        created.append(_backend(tmp_path))
        with original_lock(scope):
            yield

    monkeypatch.setattr(offload_storage_lifecycle, "conversation_deletion_lock", create_before_lock)

    # When
    receipt = delete_conversation_offloads(
        tmp_path,
        owner_id="user-a",
        conversation_id="conversation-a",
    )

    # Then
    assert receipt.history_files == 0 and receipt.spill_files == 0
    assert len(created) == 1
    assert not created[0]._current_spill_root.exists()
    with pytest.raises(OffloadSecurityError, match="deleted"):
        _backend(tmp_path)


def test_run_gc_preserves_run_created_after_absent_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    backend = _backend(tmp_path)
    assert backend.gc_spill_run().removed_runs == 1
    original_lock = offload_storage_lifecycle.conversation_mutation_lock
    created: list[ScopedOffloadBackend] = []

    @contextmanager
    def create_before_lock(scope: OffloadMutationScope) -> Iterator[None]:
        created.append(_backend(tmp_path))
        with original_lock(scope):
            yield

    monkeypatch.setattr(offload_storage_lifecycle, "conversation_mutation_lock", create_before_lock)

    # When
    receipt = backend.gc_spill_run()

    # Then
    assert (receipt.removed_runs, receipt.removed_files) == (0, 0)
    assert len(created) == 1
    assert created[0]._current_run_root.is_dir()


def test_deletion_fence_creation_fsyncs_fence_and_lock_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    backend = _backend(tmp_path)
    observed: list[tuple[int, int]] = []
    original_fsync = os.fsync

    def record_fsync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        observed.append((metadata.st_dev, metadata.st_ino))
        original_fsync(descriptor)

    monkeypatch.setattr(offload_storage_lock.os, "fsync", record_fsync)

    # When
    with offload_storage_lock.conversation_deletion_lock(backend._mutation_scope):
        pass

    # Then
    lock_root = tmp_path / ".moldy-internal" / "gc-lock"
    fence = next(lock_root.glob("*.deleted"))
    expected = {
        (lock_root.stat().st_dev, lock_root.stat().st_ino),
        (fence.stat().st_dev, fence.stat().st_ino),
    }
    assert observed == [
        (fence.stat().st_dev, fence.stat().st_ino),
        (lock_root.stat().st_dev, lock_root.stat().st_ino),
    ]
    assert set(observed) == expected
