from __future__ import annotations

import multiprocessing
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Event as ProcessEvent
from pathlib import Path
from threading import Event
from uuid import UUID

import pytest
from deepagents.backends.protocol import DeleteResult, EditResult

from app.agent_runtime import (
    offload_storage_backends,
    offload_storage_cleanup,
    offload_storage_secure_backend,
)
from app.agent_runtime.offload_storage import (
    OffloadIdentity,
    OffloadSecurityError,
    ScopedOffloadBackend,
    ScopedOffloadStorage,
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


def _observe_mutation_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> Event:
    attempted = Event()
    original = offload_storage_secure_backend.conversation_mutation_lock

    @contextmanager
    def observed(scope: OffloadMutationScope) -> Iterator[None]:
        attempted.set()
        with original(scope):
            yield

    monkeypatch.setattr(
        offload_storage_secure_backend,
        "conversation_mutation_lock",
        observed,
    )
    monkeypatch.setattr(
        offload_storage_backends,
        "conversation_mutation_lock",
        observed,
    )
    return attempted


def _assert_waiting(attempted: Event, done: Callable[[], bool]) -> None:
    assert attempted.wait(timeout=10)
    assert not done()


def test_write_waits_for_run_gc_then_creates_a_new_run_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _backend(tmp_path)
    path = _spill_path(backend)
    assert backend.write(path, "old").error is None
    entered, release = _pause_cleanup(monkeypatch)
    attempted = _observe_mutation_attempt(monkeypatch)

    with ThreadPoolExecutor(max_workers=2) as executor:
        cleanup = executor.submit(backend.gc_spill_run)
        assert entered.wait(timeout=10)
        writer = executor.submit(backend.write, path, "new")
        _assert_waiting(attempted, writer.done)
        release.set()
        assert cleanup.result(timeout=10).removed_files == 1
        assert writer.result(timeout=10).error is None

    assert backend.read(path).file_data == {"content": "new", "encoding": "utf-8"}


def test_upload_waits_for_run_gc_then_creates_a_new_run_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _backend(tmp_path)
    old_path = _spill_path(backend, "old")
    new_path = _spill_path(backend, "uploaded")
    assert backend.write(old_path, "old").error is None
    entered, release = _pause_cleanup(monkeypatch)
    attempted = _observe_mutation_attempt(monkeypatch)

    with ThreadPoolExecutor(max_workers=2) as executor:
        cleanup = executor.submit(backend.gc_spill_run)
        assert entered.wait(timeout=10)
        upload = executor.submit(backend.upload_files, [(new_path, b"uploaded")])
        _assert_waiting(attempted, upload.done)
        release.set()
        assert cleanup.result(timeout=10).removed_files == 1
        assert upload.result(timeout=10)[0].error is None

    assert backend.download_files([new_path])[0].content == b"uploaded"


@pytest.mark.parametrize("operation", ["edit", "delete"])
def test_edit_and_delete_wait_for_history_gc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    backend = _backend(tmp_path)
    path = _history_path(backend)
    assert backend.write(path, "old").error is None
    entered, release = _pause_cleanup(monkeypatch)
    attempted = _observe_mutation_attempt(monkeypatch)

    def mutate() -> EditResult | DeleteResult:
        if operation == "edit":
            return backend.edit(path, "old", "new")
        return backend.delete(path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        cleanup = executor.submit(backend.gc_history_conversation)
        assert entered.wait(timeout=10)
        mutation = executor.submit(mutate)
        _assert_waiting(attempted, mutation.done)
        release.set()
        assert cleanup.result(timeout=10).removed_files == 1
        assert mutation.result(timeout=10).error is not None

    assert backend.read(path).error is not None


def test_spill_conversation_gc_holds_one_lock_across_list_and_all_removals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _backend(tmp_path, run="run-1")
    second = _backend(tmp_path, run="run-2")
    assert first.write(_spill_path(first, "first"), "one").error is None
    assert second.write(_spill_path(second, "second"), "two").error is None
    entered, release = _pause_cleanup(monkeypatch)
    attempted = _observe_mutation_attempt(monkeypatch)

    with ThreadPoolExecutor(max_workers=2) as executor:
        cleanup = executor.submit(
            second.gc_spill_conversation,
            protected_run_ids=frozenset(),
        )
        assert entered.wait(timeout=10)
        writer = executor.submit(second.write, _spill_path(second, "after"), "new")
        _assert_waiting(attempted, writer.done)
        release.set()
        receipt = cleanup.result(timeout=10)
        assert writer.result(timeout=10).error is None

    assert (receipt.removed_runs, receipt.removed_files) == (2, 2)
    assert second.read(_spill_path(second, "after")).file_data


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


def test_process_writer_waits_for_conversation_mutation_lock(tmp_path: Path) -> None:
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

    with offload_storage_cleanup.conversation_mutation_lock(backend._mutation_scope):
        start.set()
        assert attempted.wait(timeout=10)
        assert not completed.is_set()

    worker.join(timeout=15)
    assert worker.exitcode == 0 and completed.is_set()
    assert results.get(timeout=2) is None
