from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from app.agent_runtime.offload_storage import (
    OffloadIdentity,
    ScopedOffloadBackend,
    ScopedOffloadStorage,
)
from app.agent_runtime.offload_storage_backends import HistoryBackend, SpillBackend


def _backend(data_dir: Path) -> ScopedOffloadBackend:
    return ScopedOffloadStorage(
        data_dir=data_dir,
        identity=OffloadIdentity(
            owner_id="user-a",
            conversation_id="conversation-a",
            run_id="run-a",
        ),
    ).for_actor(UUID("11111111-1111-4111-8111-111111111111"))


def _routes(
    backend: ScopedOffloadBackend,
) -> tuple[tuple[HistoryBackend, str], tuple[SpillBackend, str]]:
    history_prefix = f"{backend.artifacts_root}/conversation_history/"
    spill_prefix = f"{backend.artifacts_root}/large_tool_results/"
    history = backend.routes[history_prefix]
    spill = backend.routes[spill_prefix]
    assert isinstance(history, HistoryBackend)
    assert isinstance(spill, SpillBackend)
    return (
        (history, "/session_0123456789abcdef0123456789abcdef.md"),
        (spill, "/tool-call"),
    )


@pytest.mark.parametrize("route_index", [0, 1], ids=["history", "spill"])
def test_hashed_route_bulk_search_is_denied(route_index: int, tmp_path: Path) -> None:
    # Given
    backend = _backend(tmp_path)
    route, logical_path = _routes(backend)[route_index]
    assert route.write(logical_path, "private secret").error is None

    # When
    listing = route.ls("/")
    globbed = route.glob("**/*", path="/")
    searched = route.grep("private secret", path=logical_path)

    # Then
    assert listing.error and listing.entries is None
    assert globbed.error and globbed.matches is None
    assert searched.error and searched.matches is None
    assert "private secret" not in f"{listing.error}{globbed.error}{searched.error}"


@pytest.mark.parametrize("route_index", [0, 1], ids=["history", "spill"])
def test_hashed_route_exact_reference_read_and_download_remain_available(
    route_index: int, tmp_path: Path
) -> None:
    # Given
    backend = _backend(tmp_path)
    route, logical_path = _routes(backend)[route_index]
    assert route.write(logical_path, "private exact content").error is None

    # When
    read = route.read(logical_path)
    downloaded = route.download_files([logical_path])[0]

    # Then
    assert read.file_data == {"content": "private exact content", "encoding": "utf-8"}
    assert downloaded.content == b"private exact content"


@pytest.mark.parametrize("route_index", [0, 1], ids=["history", "spill"])
def test_hashed_route_bulk_search_rejects_root_symlink_swap(
    route_index: int, tmp_path: Path
) -> None:
    # Given
    backend = _backend(tmp_path)
    route, logical_path = _routes(backend)[route_index]
    physical = route._resolve_path(logical_path)  # noqa: SLF001
    parked = route.cwd.with_name(f"{route.cwd.name}-parked")
    route.cwd.rename(parked)
    outside = tmp_path / f"outside-{route_index}"
    outside.mkdir()
    (outside / physical.name).write_text("sibling secret")
    route.cwd.symlink_to(outside, target_is_directory=True)

    # When
    globbed = route.glob("*", path="/")
    searched = route.grep("sibling secret", path=logical_path)

    # Then
    assert globbed.error and globbed.matches is None
    assert searched.error and searched.matches is None
    assert "sibling secret" not in f"{globbed.error}{searched.error}"


@pytest.mark.parametrize("route_index", [0, 1], ids=["history", "spill"])
def test_hashed_route_bulk_search_rejects_directory_rename_swap(
    route_index: int, tmp_path: Path
) -> None:
    # Given
    backend = _backend(tmp_path)
    route, logical_path = _routes(backend)[route_index]
    physical = route._resolve_path(logical_path)  # noqa: SLF001
    parked = route.cwd.with_name(f"{route.cwd.name}-parked")
    route.cwd.rename(parked)
    sibling = tmp_path / f"sibling-{route_index}"
    sibling.mkdir()
    (sibling / physical.name).write_text("renamed sibling secret")
    sibling.rename(route.cwd)

    # When
    globbed = route.glob("*", path="/")
    searched = route.grep("renamed sibling secret", path=logical_path)

    # Then
    assert globbed.error and globbed.matches is None
    assert searched.error and searched.matches is None
    assert "renamed sibling secret" not in f"{globbed.error}{searched.error}"
