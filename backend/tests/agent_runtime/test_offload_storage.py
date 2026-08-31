from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.agent_runtime.offload_storage import (
    GENERAL_PURPOSE_ACTOR,
    LegacyHistoryKind,
    OffloadIdentity,
    OffloadSecurityError,
    ScopedOffloadStorage,
)
from app.agent_runtime.offload_storage_types import OffloadMutationScope


def _storage(
    data_dir: Path,
    *,
    owner: str = "user-a",
    conversation: str = "conversation-a",
    run: str = "run-a",
    verified_legacy_sessions: frozenset[str] = frozenset(),
) -> ScopedOffloadStorage:
    return ScopedOffloadStorage(
        data_dir=data_dir,
        identity=OffloadIdentity(
            owner_id=owner,
            conversation_id=conversation,
            run_id=run,
            verified_legacy_sessions=verified_legacy_sessions,
        ),
    )


def _actor(value: str = "11111111-1111-4111-8111-111111111111") -> UUID:
    return UUID(value)


def test_storage_isolates_two_users_siblings_parent_child_and_general_purpose(
    tmp_path: Path,
) -> None:
    # Given
    parent = _storage(tmp_path).for_actor(_actor())
    child = parent.for_actor(_actor("22222222-2222-4222-8222-222222222222"))
    sibling = _storage(tmp_path, conversation="conversation-b").for_actor(_actor())
    other_user = _storage(tmp_path, owner="user-b").for_actor(_actor())
    general = _storage(tmp_path).for_actor(GENERAL_PURPOSE_ACTOR)
    backends = (parent, child, sibling, other_user, general)

    # When
    for index, backend in enumerate(backends):
        path = f"{backend.artifacts_root}/large_tool_results/shared-leaf"
        assert backend.write(path, str(index)).error is None

    # Then
    parent_path = f"{parent.artifacts_root}/large_tool_results/shared-leaf"
    assert parent.read(parent_path).file_data == {"content": "0", "encoding": "utf-8"}
    assert parent.read(parent_path.replace(parent.artifacts_root, child.artifacts_root)).error
    physical = list((tmp_path / ".moldy-internal" / "offload" / "spill").rglob("*"))
    assert all(raw not in item.as_posix() for item in physical for raw in ("user-a", "run-a"))


def test_resume_reads_prior_run_and_mutates_only_current_run(tmp_path: Path) -> None:
    # Given
    first = _storage(tmp_path, run="run-1").for_actor(_actor())
    prior_path = f"{first.artifacts_root}/large_tool_results/tool-call"
    assert first.write(prior_path, "prior").error is None
    resumed = _storage(tmp_path, run="run-2").for_actor(_actor())

    # When
    current_path = f"{resumed.artifacts_root}/large_tool_results/current-call"
    assert resumed.write(current_path, "current").error is None
    assert resumed.delete(current_path).error is None

    # Then
    assert resumed.read(prior_path).file_data == {"content": "prior", "encoding": "utf-8"}
    assert first.read(prior_path).file_data == {"content": "prior", "encoding": "utf-8"}


def test_two_compactions_keep_history_across_runs(tmp_path: Path) -> None:
    # Given
    first = _storage(tmp_path, run="run-1").for_actor(_actor())
    path = (
        f"{first.artifacts_root}/conversation_history/session_0123456789abcdef0123456789abcdef.md"
    )
    assert first.write(path, "first").error is None
    second = _storage(tmp_path, run="run-2").for_actor(_actor())

    # When
    assert second.edit(path, "first", "first\nsecond").error is None
    assert second.edit(path, "second", "second\nthird").error is None

    # Then
    assert second.download_files([path])[0].content == b"first\nsecond\nthird"
    assert first.project_reference(path) == second.project_reference(path)


def test_reserved_roots_cannot_be_enumerated_or_forged(tmp_path: Path) -> None:
    # Given
    backend = _storage(tmp_path).for_actor(_actor())
    path = (
        f"{backend.artifacts_root}/conversation_history/session_0123456789abcdef0123456789abcdef.md"
    )
    assert backend.write(path, "secret").error is None

    # When / Then
    assert backend.ls(backend.artifacts_root).error
    assert backend.glob("**/*", path="/").matches == []
    assert backend.grep("secret", path="/").matches == []
    assert backend.read("/.moldy-internal/offload/history/forged").error
    assert backend.read(path.replace(backend.artifacts_root, "/.moldy-offload/forged")).error
    assert backend.write(f"{backend.artifacts_root}/large_tool_results/../escape", "x").error
    with pytest.raises(OffloadSecurityError):
        _storage(tmp_path).for_actor("short-name")


def test_separate_gc_preserves_history_and_user_artifacts(tmp_path: Path) -> None:
    # Given
    first = _storage(tmp_path, run="run-1").for_actor(_actor())
    second = _storage(tmp_path, run="run-2").for_actor(_actor())
    prior = f"{first.artifacts_root}/large_tool_results/prior"
    current = f"{second.artifacts_root}/large_tool_results/current"
    history = (
        f"{first.artifacts_root}/conversation_history/session_0123456789abcdef0123456789abcdef.md"
    )
    artifact = tmp_path / "artifacts" / "sentinel.txt"
    artifact.parent.mkdir()
    artifact.write_text("keep")
    assert first.write(prior, "prior").error is None
    assert second.write(current, "current").error is None
    assert first.write(history, "history").error is None

    # When
    spill_gc = second.gc_spill_conversation(protected_run_ids=frozenset({"run-1"}))
    history_gc = first.gc_history_conversation()

    # Then
    assert (spill_gc.removed_runs, spill_gc.removed_files) == (1, 1)
    assert first.read(prior).file_data
    assert second.read(current).error
    assert history_gc.removed_files == 1
    assert artifact.read_text() == "keep"


def test_concurrent_spill_gc_claims_one_run_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    backend = _storage(tmp_path).for_actor(_actor())
    assert (
        backend.write(f"{backend.artifacts_root}/large_tool_results/tool", "secret").error is None
    )
    from app.agent_runtime import offload_storage_lifecycle

    original_lock = offload_storage_lifecycle.conversation_mutation_lock
    callers = 8
    barrier = Barrier(callers)

    @contextmanager
    def lock_together(scope: OffloadMutationScope) -> Iterator[None]:
        barrier.wait(timeout=10)
        with original_lock(scope):
            yield

    monkeypatch.setattr(offload_storage_lifecycle, "conversation_mutation_lock", lock_together)

    # When
    with ThreadPoolExecutor(max_workers=callers) as executor:
        receipts = list(executor.map(lambda _: backend.gc_spill_run(), range(callers)))

    # Then
    assert sorted(receipt.removed_runs for receipt in receipts) == [0] * (callers - 1) + [1]
    assert sum(receipt.removed_files for receipt in receipts) == 1


def test_spill_gc_of_absent_run_is_a_zero_receipt(tmp_path: Path) -> None:
    # Given
    backend = _storage(tmp_path).for_actor(_actor())

    # When
    first = backend.gc_spill_run()
    second = backend.gc_spill_run()

    # Then
    assert (first.removed_runs, first.removed_files) == (1, 0)
    assert (second.removed_runs, second.removed_files) == (0, 0)


def test_concurrent_spill_gc_of_different_runs_is_independent(tmp_path: Path) -> None:
    # Given
    first = _storage(tmp_path, run="run-1").for_actor(_actor())
    second = _storage(tmp_path, run="run-2").for_actor(_actor())
    for index, backend in enumerate((first, second), start=1):
        for file_index in range(index):
            path = f"{backend.artifacts_root}/large_tool_results/run-{index}-tool-{file_index}"
            assert backend.write(path, f"result-{index}-{file_index}").error is None

    # When
    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(lambda backend: backend.gc_spill_run(), (first, second)))

    # Then
    assert sorted((receipt.removed_runs, receipt.removed_files) for receipt in receipts) == [
        (1, 1),
        (1, 2),
    ]


@pytest.mark.parametrize(
    ("kind", "filename"),
    [
        (LegacyHistoryKind.SESSION, "session_0123456789abcdef0123456789abcdef.md"),
        (LegacyHistoryKind.CONVERSATION, "conversation-a.md"),
    ],
)
def test_legacy_migration_keeps_source_is_idempotent_and_hashes_content(
    tmp_path: Path, kind: LegacyHistoryKind, filename: str
) -> None:
    # Given
    source = tmp_path / "conversation_history" / filename
    source.parent.mkdir()
    source.write_text("legacy-history")
    verified = frozenset({filename}) if kind is LegacyHistoryKind.SESSION else frozenset()
    backend = _storage(tmp_path, verified_legacy_sessions=verified).for_actor(_actor())

    # When
    first = backend.migrate_legacy_history(kind, source_filename=filename)
    second = backend.migrate_legacy_history(kind, source_filename=filename)

    # Then
    assert first == second
    assert first.content_sha256 == second.content_sha256
    assert source.read_text() == "legacy-history"
    assert backend.download_files([first.virtual_path])[0].content == b"legacy-history"


@patch("app.agent_runtime.runtime_component_builder.create_deep_agent")
def test_build_agent_scopes_parent_linked_and_general_purpose_actors(
    mock_create: MagicMock, tmp_path: Path
) -> None:
    # Given
    from app.agent_runtime.runtime_component_builder import build_agent

    parent = _storage(tmp_path).for_actor(_actor())
    child = {
        "name": "linked-child",
        "description": "trusted linked child",
        "system_prompt": "help",
        "_moldy_actor_id": "22222222-2222-4222-8222-222222222222",
    }

    # When
    build_agent(
        FakeListChatModel(responses=["done"]), [], "prompt", backend=parent, subagents=[child]
    )

    # Then
    call = mock_create.call_args.kwargs
    general, linked = call["subagents"]
    assert call["backend"] is parent
    assert call["middleware"][0].backend is parent
    assert (general["name"], linked["name"], "_moldy_actor_id" in linked) == (
        GENERAL_PURPOSE_ACTOR,
        "linked-child",
        False,
    )
    actor_roots = {
        parent.artifacts_root,
        general["middleware"][0].backend.artifacts_root,
        linked["middleware"][0].backend.artifacts_root,
    }
    assert len(actor_roots) == 3
    for subagent in (general, linked):
        filesystem = subagent["middleware"][0]
        summary = next(
            item for item in subagent["middleware"] if item.name == "SummarizationMiddleware"
        )
        assert summary._backend is filesystem.backend
