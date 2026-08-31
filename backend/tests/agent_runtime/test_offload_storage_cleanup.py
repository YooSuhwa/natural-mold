from __future__ import annotations

import multiprocessing
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Barrier as ProcessBarrier
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest

from app.agent_runtime import offload_storage_cleanup, offload_storage_lifecycle
from app.agent_runtime.offload_storage import (
    OffloadIdentity,
    OffloadSecurityError,
    ScopedOffloadBackend,
    ScopedOffloadStorage,
    delete_conversation_offloads,
)


def _backend(
    data_dir: Path,
    *,
    owner: str = "user-a",
    conversation: str = "conversation-a",
) -> ScopedOffloadBackend:
    storage = ScopedOffloadStorage(
        data_dir=data_dir,
        identity=OffloadIdentity(
            owner_id=owner,
            conversation_id=conversation,
            run_id="run-a",
        ),
    )
    return storage.for_actor(UUID("11111111-1111-4111-8111-111111111111"))


def _write_spill(backend: ScopedOffloadBackend, name: str, content: str = "secret") -> None:
    path = f"{backend.artifacts_root}/large_tool_results/{name}"
    assert backend.write(path, content).error is None


def _process_gc_run(
    data_dir: str,
    start: ProcessBarrier,
    results: Queue[tuple[int, int]],
) -> None:
    backend = _backend(Path(data_dir))
    start.wait(timeout=10)
    receipt = backend.gc_spill_run()
    results.put((receipt.removed_runs, receipt.removed_files))


def test_processes_gc_one_run_exactly_once(tmp_path: Path) -> None:
    # Given
    backend = _backend(tmp_path)
    _write_spill(backend, "tool")
    context = multiprocessing.get_context("spawn")
    start = context.Barrier(4)
    results = context.Queue()
    workers = [
        context.Process(target=_process_gc_run, args=(str(tmp_path), start, results))
        for _ in range(4)
    ]

    # When
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=15)

    # Then
    assert all(worker.exitcode == 0 for worker in workers)
    receipts = [results.get(timeout=2) for _ in workers]
    assert sorted(receipts) == [(0, 0), (0, 0), (0, 0), (1, 1)]


def test_gc_lock_is_persistent_opaque_and_outside_offload(tmp_path: Path) -> None:
    # Given
    owner = "raw-owner-id"
    conversation = "raw-conversation-id"
    backend = _backend(tmp_path, owner=owner, conversation=conversation)

    # When
    backend.gc_spill_run()

    # Then
    locks = list((tmp_path / ".moldy-internal" / "gc-lock").iterdir())
    assert len(locks) == 1
    assert locks[0].is_file() and locks[0].stat().st_size == 0
    assert owner not in locks[0].name and conversation not in locks[0].name
    offload = tmp_path / ".moldy-internal" / "offload"
    assert not list(offload.rglob("*.lock")) and not list(offload.rglob("*.gc-*"))


def test_gc_mid_delete_failure_keeps_original_path_and_retry_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    backend = _backend(tmp_path)
    for name in ("first", "second"):
        _write_spill(backend, name, name)
    target = backend._current_run_root
    original_unlink = os.unlink
    calls = 0

    def fail_second(name: str, *, dir_fd: int | None = None) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected cleanup failure")
        original_unlink(name, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", fail_second)

    # When / Then
    with pytest.raises(OSError, match="injected"):
        backend.gc_spill_run()
    assert target.is_dir() and not list(target.parent.glob(f".{target.name}.gc-*"))

    monkeypatch.setattr(os, "unlink", original_unlink)
    receipt = backend.gc_spill_run()
    assert (receipt.removed_runs, receipt.removed_files) == (1, 1)
    assert not target.exists()


def test_run_gc_and_conversation_delete_do_not_duplicate_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    backend = _backend(tmp_path)
    for name in ("first", "second"):
        _write_spill(backend, name, name)
    history = f"{backend.artifacts_root}/conversation_history/session_{'0' * 32}.md"
    assert backend.write(history, "history").error is None
    original_mutation_lock = offload_storage_lifecycle.conversation_mutation_lock
    original_deletion_lock = offload_storage_lifecycle.conversation_deletion_lock
    barrier = Barrier(2)

    @contextmanager
    def mutate_together(
        scope: offload_storage_cleanup.OffloadMutationScope,
    ) -> Iterator[None]:
        barrier.wait(timeout=10)
        with original_mutation_lock(scope):
            yield

    @contextmanager
    def delete_together(
        scope: offload_storage_cleanup.OffloadMutationScope,
    ) -> Iterator[None]:
        barrier.wait(timeout=10)
        with original_deletion_lock(scope):
            yield

    monkeypatch.setattr(offload_storage_lifecycle, "conversation_mutation_lock", mutate_together)
    monkeypatch.setattr(offload_storage_lifecycle, "conversation_deletion_lock", delete_together)

    # When
    with ThreadPoolExecutor(max_workers=2) as executor:
        run = executor.submit(backend.gc_spill_run)
        conversation = executor.submit(
            delete_conversation_offloads,
            tmp_path,
            owner_id="user-a",
            conversation_id="conversation-a",
        )
        conversation_receipt = conversation.result(timeout=15)
        try:
            run_receipt = run.result(timeout=15)
        except OffloadSecurityError as error:
            assert "deleted" in str(error)
            run_files = 0
        else:
            run_files = run_receipt.removed_files

    # Then
    assert run_files + conversation_receipt.spill_files == 2
    assert conversation_receipt.history_files == 1
    assert not backend._spill_conversation_root.exists()
    assert not list((tmp_path / ".moldy-internal" / "offload").rglob("*.gc-*"))
