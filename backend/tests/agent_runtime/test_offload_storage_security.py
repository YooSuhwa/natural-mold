from __future__ import annotations

import os
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.agent_runtime import offload_storage_fd, offload_storage_lock
from app.agent_runtime.offload_storage import (
    LegacyHistoryKind,
    OffloadIdentity,
    OffloadSecurityError,
    ScopedOffloadBackend,
    ScopedOffloadStorage,
)
from app.agent_runtime.offload_storage_backends import HistoryBackend
from app.agent_runtime.offload_storage_io import atomic_write_new_or_equal


def _backend(data_dir: Path, *, verified: frozenset[str] = frozenset()) -> ScopedOffloadBackend:
    return ScopedOffloadStorage(
        data_dir=data_dir,
        identity=OffloadIdentity(
            owner_id="user-a",
            conversation_id="conversation-a",
            run_id="run-a",
            verified_legacy_sessions=verified,
        ),
    ).for_actor(UUID("11111111-1111-4111-8111-111111111111"))


def _history(backend: ScopedOffloadBackend) -> tuple[str, Path]:
    filename = "session_0123456789abcdef0123456789abcdef.md"
    path = f"{backend.artifacts_root}/conversation_history/{filename}"
    route = backend.routes[f"{backend.artifacts_root}/conversation_history/"]
    assert isinstance(route, HistoryBackend)
    return path, route._resolve_path(f"/{filename}")  # noqa: SLF001


@pytest.mark.parametrize(
    ("module", "attribute", "value"),
    [
        (offload_storage_fd, "O_NOFOLLOW", None),
        (offload_storage_fd, "O_DIRECTORY", None),
        (offload_storage_fd, "DIR_FD_CAPABILITIES", False),
        (offload_storage_lock, "_FCNTL", None),
    ],
)
def test_missing_secure_platform_capability_creates_no_offload_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    attribute: str,
    value: int | bool | None,
) -> None:
    monkeypatch.setattr(module, attribute, value)

    with pytest.raises(
        OffloadSecurityError,
        match="secure offload filesystem operations are unavailable",
    ):
        _backend(tmp_path)

    assert not (tmp_path / ".moldy-internal" / "offload").exists()


def test_storage_creation_rejects_symlink_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    outside = tmp_path / "outside"
    outside.mkdir()
    original_mkdir = os.mkdir
    installed = False

    def install(path: str, mode: int = 0o777, *, dir_fd: int | None = None) -> None:
        nonlocal installed
        if not installed and Path(path).name == ".moldy-internal":
            installed = True
            os.symlink(outside, path, dir_fd=dir_fd)
            raise FileExistsError
        original_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", install)

    # When / Then
    with pytest.raises(OffloadSecurityError, match="symlink"):
        _backend(tmp_path)
    assert not list(outside.rglob("*"))


def test_post_construction_symlink_swap_cannot_redirect_io(tmp_path: Path) -> None:
    # Given
    backend = _backend(tmp_path)
    history, _ = _history(backend)
    spill = f"{backend.artifacts_root}/large_tool_results/tool"
    assert backend.write(history, "history").error is None
    assert backend.write(spill, "spill").error is None
    root = backend._internal_root
    root.rename(root.with_name(f"{root.name}-backup"))
    outside = tmp_path / "outside"
    outside.mkdir()
    root.symlink_to(outside, target_is_directory=True)

    # When
    results = (
        backend.read(history),
        backend.write(history, "redirected"),
        backend.edit(history, "history", "redirected"),
        backend.download_files([history])[0],
        backend.upload_files([(history, b"redirected")])[0],
        backend.delete(history),
        backend.read(spill),
        backend.write(spill, "redirected"),
    )

    # Then
    assert all(result.error for result in results)
    assert not list(outside.rglob("*"))


@pytest.mark.parametrize("operation", ["read", "write", "edit", "delete", "download", "upload"])
def test_operations_reject_external_hardlinks(tmp_path: Path, operation: str) -> None:
    # Given
    backend = _backend(tmp_path)
    path, physical = _history(backend)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    os.link(outside, physical)

    # When
    match operation:
        case "read":
            error = backend.read(path).error
        case "write":
            error = backend.write(path, "replacement").error
        case "edit":
            error = backend.edit(path, "outside", "replacement").error
        case "delete":
            error = backend.delete(path).error
        case "download":
            error = backend.download_files([path])[0].error
        case "upload":
            error = backend.upload_files([(path, b"replacement")])[0].error
        case unreachable:
            raise AssertionError(unreachable)

    # Then
    assert error and str(physical) not in error and physical.name not in error
    assert outside.read_text() == "outside"


def test_write_rejects_hardlink_create_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    backend = _backend(tmp_path)
    path, physical = _history(backend)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    original_open = os.open
    installed = False

    def install(path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        nonlocal installed
        if not installed and path.startswith(f".{physical.name}.") and flags & os.O_EXCL:
            installed = True
            os.link(outside, physical.name, dst_dir_fd=dir_fd)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", install)

    # When / Then
    result = backend.write(path, "replacement")
    assert installed and result.error and outside.read_text() == "outside"


@pytest.mark.parametrize("operation", ["write", "edit", "upload"])
def test_updates_replace_raced_hardlink_without_mutating_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    # Given
    backend = _backend(tmp_path)
    path, physical = _history(backend)
    assert backend.write(path, "private").error is None
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    original_replace = os.replace
    raced = False

    def install(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal raced
        if not raced and destination == physical.name:
            raced = True
            os.unlink(destination, dir_fd=dst_dir_fd)
            os.link(outside, destination, dst_dir_fd=dst_dir_fd)
        original_replace(source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(os, "replace", install)

    # When
    match operation:
        case "write":
            error = backend.write(path, "replacement").error
        case "edit":
            error = backend.edit(path, "private", "replacement").error
        case "upload":
            error = backend.upload_files([(path, b"replacement")])[0].error
        case unreachable:
            raise AssertionError(unreachable)

    # Then
    assert raced and error is None and outside.read_text() == "outside"
    assert physical.read_text() == "replacement"


@pytest.mark.parametrize("destination_linked", [False, True])
def test_migration_rejects_hardlinked_source_or_destination(
    tmp_path: Path, destination_linked: bool
) -> None:
    # Given
    filename = "session_0123456789abcdef0123456789abcdef.md"
    backend = _backend(tmp_path, verified=frozenset({filename}))
    _, destination = _history(backend)
    source = tmp_path / "conversation_history" / filename
    source.parent.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    if destination_linked:
        source.write_text("legacy")
        os.link(outside, destination)
    else:
        os.link(outside, source)

    # When / Then
    with pytest.raises(OffloadSecurityError) as captured:
        backend.migrate_legacy_history(LegacyHistoryKind.SESSION, source_filename=filename)
    assert str(outside) not in str(captured.value)
    assert outside.read_text() == "outside"


def test_atomic_migration_install_cannot_clobber_noncooperative_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    destination = tmp_path / "leaf"
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    original_link = os.link
    raced = False

    def install(
        source: str,
        target: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        nonlocal raced
        if not raced and target == destination.name:
            raced = True
            original_link(outside, target, dst_dir_fd=dst_dir_fd)
        original_link(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(os, "link", install)

    # When / Then
    with pytest.raises(OffloadSecurityError):
        atomic_write_new_or_equal(destination, b"migration")
    assert raced and destination.read_bytes() == outside.read_bytes()


@patch("app.agent_runtime.runtime_component_builder.create_deep_agent")
def test_build_agent_rejects_untrusted_child_actor(mock_create: MagicMock, tmp_path: Path) -> None:
    # Given
    from app.agent_runtime.runtime_component_builder import build_agent

    # When / Then
    with pytest.raises(ValueError, match="trusted actor identity"):
        build_agent(
            FakeListChatModel(responses=["done"]),
            [],
            "prompt",
            backend=_backend(tmp_path),
            subagents=[{"name": "child", "description": "child", "system_prompt": "help"}],
        )
    mock_create.assert_not_called()
