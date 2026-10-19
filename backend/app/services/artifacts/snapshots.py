"""Output-dir snapshots and delta diffing for artifact recording (split from ``recorder``).

Moved verbatim (I-08 file-size split); ``recorder`` re-exports every name, so the
``artifact_service`` facade and its monkeypatch surface are unchanged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.services.artifact_paths import (
    ArtifactPathError,
    NormalizedArtifactPath,
    normalize_output_path,
)
from app.services.artifacts.summary import FileEventOperation


@dataclass(frozen=True)
class ArtifactFileState:
    path: Path
    normalized: NormalizedArtifactPath
    size_bytes: int
    mtime_ns: int
    ctime_ns: int
    inode: int
    sha256: str | None


@dataclass(frozen=True)
class ArtifactSnapshot:
    files: dict[str, ArtifactFileState]


@dataclass(frozen=True)
class ArtifactDelta:
    op: FileEventOperation
    state: ArtifactFileState


async def snapshot_output_dir(
    base_dir: Path,
    *,
    previous: ArtifactSnapshot | None = None,
    hash_files: bool = True,
) -> ArtifactSnapshot:
    # Call-time facade lookup: tests monkeypatch artifact_service._sha256_file
    # to observe hashing; a top-level import of the concrete module would
    # early-bind and silently bypass that patch.
    from app.services.artifact_service import _sha256_file

    base_dir = Path(base_dir)
    if not base_dir.exists():
        return ArtifactSnapshot(files={})

    files: dict[str, ArtifactFileState] = {}
    for path in sorted(base_dir.rglob("*")):
        try:
            normalized = normalize_output_path(base_dir, path)
        except ArtifactPathError:
            continue

        stat = path.stat()
        if stat.st_size > settings.artifact_max_bytes:
            continue
        previous_state = previous.files.get(normalized.logical_path) if previous else None
        if previous_state is not None and _state_matches_stat(previous_state, stat):
            sha256 = previous_state.sha256
        elif hash_files:
            sha256 = await _sha256_file(path)
        else:
            sha256 = None

        files[normalized.logical_path] = ArtifactFileState(
            path=path,
            normalized=normalized,
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            ctime_ns=stat.st_ctime_ns,
            inode=int(getattr(stat, "st_ino", 0)),
            sha256=sha256,
        )

    return ArtifactSnapshot(files=files)


def _state_matches_stat(state: ArtifactFileState, stat: os.stat_result) -> bool:
    return (
        state.size_bytes == stat.st_size
        and state.mtime_ns == stat.st_mtime_ns
        and state.ctime_ns == stat.st_ctime_ns
        and state.inode == int(getattr(stat, "st_ino", 0))
    )


def _states_have_same_metadata(left: ArtifactFileState, right: ArtifactFileState) -> bool:
    return (
        left.size_bytes == right.size_bytes
        and left.mtime_ns == right.mtime_ns
        and left.ctime_ns == right.ctime_ns
        and left.inode == right.inode
    )


def diff_snapshots(before: ArtifactSnapshot, after: ArtifactSnapshot) -> list[ArtifactDelta]:
    deltas: list[ArtifactDelta] = []
    for logical_path, after_state in after.files.items():
        before_state = before.files.get(logical_path)
        if before_state is None:
            deltas.append(ArtifactDelta(op="created", state=after_state))
            continue
        if not _states_have_same_metadata(before_state, after_state) or (
            before_state.sha256 is not None
            and after_state.sha256 is not None
            and before_state.sha256 != after_state.sha256
        ):
            deltas.append(ArtifactDelta(op="updated", state=after_state))
    for logical_path, before_state in before.files.items():
        if logical_path not in after.files:
            deltas.append(ArtifactDelta(op="deleted", state=before_state))
    return deltas[: settings.artifact_max_files_per_run]
