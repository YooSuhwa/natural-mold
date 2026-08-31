from __future__ import annotations

import multiprocessing
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Event as ProcessEvent
from pathlib import Path
from threading import Event, Lock
from uuid import UUID

import pytest

from app.agent_runtime import offload_storage, offload_storage_cleanup
from app.agent_runtime.offload_storage import (
    LegacyHistoryKind,
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


def _spill_path(backend: ScopedOffloadBackend) -> str:
    return f"{backend.artifacts_root}/large_tool_results/tool"


def _history_path(backend: ScopedOffloadBackend) -> str:
    return f"{backend.artifacts_root}/conversation_history/session_{'0' * 32}.md"


def _pause_cleanup(monkeypatch: pytest.MonkeyPatch) -> tuple[Event, Event]:
    entered = Event()
    release = Event()
    original = offload_storage_cleanup._remove_directory_contents

    def paused(descriptor: int) -> int:
        entered.set()
        assert release.wait(timeout=10)
        return original(descriptor)

    monkeypatch.setattr(offload_storage_cleanup, "_remove_directory_contents", paused)
    return entered, release


def _process_writer(
    data_dir: str,
    ready: ProcessEvent,
    start: ProcessEvent,
    attempted: ProcessEvent,
    completed: ProcessEvent,
    results: Queue[str | None],
) -> None:
    backend = _backend(Path(data_dir))
    ready.set()
    assert start.wait(timeout=10)
    attempted.set()
    try:
        error = backend.write(_spill_path(backend), "process").error
    except OffloadSecurityError as security_error:
        error = str(security_error)
    results.put(error)
    completed.set()


def test_migration_and_constructor_wait_then_fail_after_conversation_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    source_name = f"session_{'1' * 32}.md"
    backend = _backend(tmp_path, verified=frozenset({source_name}))
    source = tmp_path / "conversation_history" / source_name
    source.parent.mkdir()
    source.write_text("legacy")
    entered, release = _pause_cleanup(monkeypatch)
    original_lock = offload_storage.conversation_mutation_lock
    attempts_lock = Lock()
    attempts = 0
    both_attempted = Event()

    @contextmanager
    def observed(scope: OffloadMutationScope) -> Iterator[None]:
        nonlocal attempts
        with attempts_lock:
            attempts += 1
            if attempts == 2:
                both_attempted.set()
        with original_lock(scope):
            yield

    monkeypatch.setattr(offload_storage, "conversation_mutation_lock", observed)

    # When
    with ThreadPoolExecutor(max_workers=3) as executor:
        deletion = executor.submit(
            delete_conversation_offloads,
            tmp_path,
            owner_id="user-a",
            conversation_id="conversation-a",
        )
        assert entered.wait(timeout=10)
        migration = executor.submit(
            backend.migrate_legacy_history,
            LegacyHistoryKind.SESSION,
            source_filename=source_name,
        )
        constructor = executor.submit(_backend, tmp_path, run="run-b")
        assert both_attempted.wait(timeout=10)
        assert not migration.done() and not constructor.done()
        release.set()
        deletion.result(timeout=10)
        with pytest.raises(OffloadSecurityError, match="deleted"):
            migration.result(timeout=10)
        with pytest.raises(OffloadSecurityError, match="deleted"):
            constructor.result(timeout=10)

    # Then
    assert backend.download_files([_history_path(backend)])[0].error is not None
    assert not backend._current_spill_root.exists()


def test_process_writer_with_existing_backend_is_fenced_after_delete(tmp_path: Path) -> None:
    # Given
    backend = _backend(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready, start = context.Event(), context.Event()
    attempted, completed = context.Event(), context.Event()
    results = context.Queue()
    worker = context.Process(
        target=_process_writer,
        args=(str(tmp_path), ready, start, attempted, completed, results),
    )
    worker.start()
    assert ready.wait(timeout=10)

    # When
    delete_conversation_offloads(
        tmp_path,
        owner_id="user-a",
        conversation_id="conversation-a",
    )
    start.set()
    worker.join(timeout=15)

    # Then
    assert worker.exitcode == 0 and attempted.is_set() and completed.is_set()
    assert results.get(timeout=2) == "conversation offload storage was deleted"
    assert not backend._current_spill_root.exists()
