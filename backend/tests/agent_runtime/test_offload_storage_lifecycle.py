from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest

from app.agent_runtime.offload_storage import (
    LegacyHistoryKind,
    OffloadIdentity,
    OffloadSecurityError,
    ScopedOffloadStorage,
    delete_conversation_offloads,
    logical_offload_id,
)
from app.agent_runtime.offload_storage_io import atomic_write_new_or_equal


def _backend(
    data_dir: Path,
    *,
    verified_legacy_sessions: frozenset[str] = frozenset(),
):
    storage = ScopedOffloadStorage(
        data_dir=data_dir,
        identity=OffloadIdentity(
            owner_id="user-a",
            conversation_id="conversation-a",
            run_id="run-a",
            verified_legacy_sessions=verified_legacy_sessions,
        ),
    )
    return storage.for_actor(UUID("11111111-1111-4111-8111-111111111111"))


def test_legacy_migration_refuses_symlink_traversal_and_unowned_source(tmp_path: Path) -> None:
    # Given
    backend = _backend(tmp_path)
    legacy_root = tmp_path / "conversation_history"
    legacy_root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside")
    (legacy_root / "session_0123456789abcdef0123456789abcdef.md").symlink_to(outside)

    # When / Then
    with pytest.raises(OffloadSecurityError):
        backend.migrate_legacy_history(
            LegacyHistoryKind.SESSION,
            source_filename="session_0123456789abcdef0123456789abcdef.md",
        )
    with pytest.raises(OffloadSecurityError):
        backend.migrate_legacy_history(LegacyHistoryKind.SESSION, source_filename="../outside.md")
    with pytest.raises(OffloadSecurityError):
        backend.migrate_legacy_history(LegacyHistoryKind.CONVERSATION, source_filename="other.md")


def test_legacy_session_migration_requires_trusted_allowlist(tmp_path: Path) -> None:
    # Given
    filename = "session_0123456789abcdef0123456789abcdef.md"
    legacy_root = tmp_path / "conversation_history"
    legacy_root.mkdir()
    (legacy_root / filename).write_text("unowned")
    backend = _backend(tmp_path)

    # When / Then
    with pytest.raises(OffloadSecurityError, match="not verified"):
        backend.migrate_legacy_history(LegacyHistoryKind.SESSION, source_filename=filename)


def test_default_backend_blocks_legacy_roots_except_verified_migration(tmp_path: Path) -> None:
    # Given
    filename = "session_0123456789abcdef0123456789abcdef.md"
    history_secret = "history-secret"
    spill_secret = "spill-secret"
    history_root = tmp_path / "conversation_history"
    spill_root = tmp_path / "large_tool_results"
    history_root.mkdir()
    spill_root.mkdir()
    (history_root / filename).write_text(history_secret)
    (spill_root / "tool-call").write_text(spill_secret)
    scoped = _backend(tmp_path, verified_legacy_sessions=frozenset({filename}))
    default = scoped.default

    # When
    root_results = (
        default.ls("/"),
        default.glob("*", "/"),
        default.grep(history_secret, "/"),
        default.grep(spill_secret, "/"),
    )
    receipt = scoped.migrate_legacy_history(LegacyHistoryKind.SESSION, source_filename=filename)
    direct_results = (
        default.read(f"/conversation_history/{filename}"),
        default.write("/conversation_history/new.md", "mutated"),
        default.download_files([f"/conversation_history/{filename}"])[0],
        default.upload_files([("/conversation_history/upload.md", b"mutated")])[0],
        default.delete(f"/conversation_history/{filename}"),
        default.read("/large_tool_results/tool-call"),
        default.write("/large_tool_results/new", "mutated"),
        default.download_files(["/large_tool_results/tool-call"])[0],
        default.upload_files([("/large_tool_results/upload", b"mutated")])[0],
        default.delete("/large_tool_results/tool-call"),
    )

    # Then
    assert receipt.content_sha256
    assert all(result.error is not None for result in direct_results)
    root_output = repr(root_results)
    direct_output = repr(direct_results)
    assert "conversation_history" not in root_output
    assert "large_tool_results" not in root_output
    assert history_secret not in root_output + direct_output
    assert spill_secret not in root_output + direct_output
    assert str(tmp_path) not in root_output + direct_output
    assert (history_root / filename).read_text() == history_secret
    assert (spill_root / "tool-call").read_text() == spill_secret


def test_concurrent_legacy_migration_is_idempotent(tmp_path: Path) -> None:
    # Given
    filename = "session_0123456789abcdef0123456789abcdef.md"
    legacy_root = tmp_path / "conversation_history"
    legacy_root.mkdir()
    (legacy_root / filename).write_text("legacy")
    backend = _backend(tmp_path, verified_legacy_sessions=frozenset({filename}))

    # When
    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(
            executor.map(
                lambda _index: backend.migrate_legacy_history(
                    LegacyHistoryKind.SESSION, source_filename=filename
                ),
                range(2),
            )
        )

    # Then
    assert receipts[0] == receipts[1]


def test_atomic_install_rejects_concurrent_different_content(tmp_path: Path) -> None:
    # Given
    destination = tmp_path / "destination"
    barrier = Barrier(2)

    def install(content: bytes) -> str:
        barrier.wait()
        try:
            atomic_write_new_or_equal(destination, content)
        except OffloadSecurityError:
            return "conflict"
        return "installed"

    # When
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(install, (b"first", b"second")))

    # Then
    assert sorted(outcomes) == ["conflict", "installed"]
    assert destination.read_bytes() in {b"first", b"second"}


def test_atomic_install_removes_temporary_after_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    destination = tmp_path / "destination"

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("injected fsync failure")

    monkeypatch.setattr(os, "fsync", fail_fsync)

    # When / Then
    with pytest.raises(OSError, match="injected fsync failure"):
        atomic_write_new_or_equal(destination, b"content")
    assert not destination.exists()
    assert list(tmp_path.glob(".*.tmp")) == []


def test_migration_receipt_install_refuses_symlink_destination(tmp_path: Path) -> None:
    # Given
    filename = "session_0123456789abcdef0123456789abcdef.md"
    legacy_root = tmp_path / "conversation_history"
    legacy_root.mkdir()
    (legacy_root / filename).write_text("legacy")
    backend = _backend(tmp_path, verified_legacy_sessions=frozenset({filename}))
    backend.migrate_legacy_history(LegacyHistoryKind.SESSION, source_filename=filename)
    receipt = next((tmp_path / ".moldy-internal").rglob("receipts/*.json"))
    receipt.unlink()
    outside = tmp_path / "outside"
    outside.write_text("keep")
    receipt.symlink_to(outside)

    # When / Then
    with pytest.raises(OffloadSecurityError, match="regular file"):
        backend.migrate_legacy_history(LegacyHistoryKind.SESSION, source_filename=filename)
    assert outside.read_text() == "keep"


def test_migration_refuses_legacy_root_symlink_swap(tmp_path: Path) -> None:
    # Given
    filename = "session_0123456789abcdef0123456789abcdef.md"
    legacy_root = tmp_path / "conversation_history"
    legacy_root.mkdir()
    (legacy_root / filename).write_text("owned")
    backend = _backend(tmp_path, verified_legacy_sessions=frozenset({filename}))
    moved_root = tmp_path / "moved-history"
    legacy_root.rename(moved_root)
    outside_root = tmp_path / "outside-history"
    outside_root.mkdir()
    (outside_root / filename).write_text("outside")
    legacy_root.symlink_to(outside_root, target_is_directory=True)

    # When / Then
    with pytest.raises(OffloadSecurityError, match="symlink"):
        backend.migrate_legacy_history(LegacyHistoryKind.SESSION, source_filename=filename)


def test_delete_conversation_offloads_is_idempotent_and_preserves_artifacts(
    tmp_path: Path,
) -> None:
    # Given
    backend = _backend(tmp_path)
    spill = f"{backend.artifacts_root}/large_tool_results/tool"
    history = (
        f"{backend.artifacts_root}/conversation_history/session_0123456789abcdef0123456789abcdef.md"
    )
    assert backend.write(spill, "spill").error is None
    assert backend.write(history, "history").error is None
    artifact = tmp_path / "artifacts" / "sentinel"
    artifact.parent.mkdir()
    artifact.write_text("keep")

    # When
    first = delete_conversation_offloads(
        tmp_path, owner_id="user-a", conversation_id="conversation-a"
    )
    second = delete_conversation_offloads(
        tmp_path, owner_id="user-a", conversation_id="conversation-a"
    )

    # Then
    assert first.history_files == 1
    assert first.spill_files == 1
    assert second.history_files == 0
    assert second.spill_files == 0
    assert artifact.read_text() == "keep"


@pytest.mark.parametrize(
    "path",
    [
        "/conversation_history/session_0123456789abcdef0123456789abcdef.md",
        "/large_tool_results/tool-call",
    ],
)
def test_projection_returns_only_opaque_logical_identity(tmp_path: Path, path: str) -> None:
    # Given
    backend = _backend(tmp_path)
    scoped_path = f"{backend.artifacts_root}{path}"

    # When
    projection = backend.project_reference(scoped_path)
    legacy_projection = backend.project_reference(path)

    # Then
    assert projection is not None
    assert projection == legacy_projection
    assert projection.logical_id.startswith(("history_", "spill_"))
    assert projection.logical_id == logical_offload_id(projection.kind, scoped_path)
    assert "/" not in projection.logical_id
    assert ".moldy" not in projection.logical_id
    assert set(asdict(projection)) == {"kind", "logical_id"}
