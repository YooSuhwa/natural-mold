"""Artifact service — delta recording, library queries, content, summaries.

BE-S8: the implementation lives in the ``app.services.artifacts`` package
(``recorder`` / ``library`` / ``content`` / ``summary`` / ``errors``); this
module is the compatibility facade and only re-exports.

Patch-contract notes (tests/test_artifact_service.py):

- ``asyncio`` is re-exported because tests monkeypatch
  ``artifact_service.asyncio.to_thread`` to observe hash offloading.
- ``_sha256_file`` must stay patchable on this facade —
  ``recorder.snapshot_output_dir`` / ``recorder.ingest_changed_files``
  resolve it through this module at call time.
"""

from __future__ import annotations

import asyncio as asyncio

from app.services.artifacts.content import (
    _read_file_prefix as _read_file_prefix,
)
from app.services.artifacts.content import (
    _sha256_file as _sha256_file,
)
from app.services.artifacts.content import (
    _sha256_file_sync as _sha256_file_sync,
)
from app.services.artifacts.content import (
    _storage_local_path as _storage_local_path,
)
from app.services.artifacts.content import (
    get_artifact_download_path,
    read_artifact_text_content,
)
from app.services.artifacts.errors import ArtifactNotFoundError
from app.services.artifacts.library import (
    LIBRARY_CURSOR_SEPARATOR,
    delete_artifact,
    get_library_stats,
    list_conversation_artifacts,
    list_conversation_artifacts_by_message_id,
    list_library_artifacts,
    list_recent_artifacts,
    record_artifact_download,
    record_artifact_opened,
    set_artifact_favorite,
)
from app.services.artifacts.library import (
    _decode_library_cursor as _decode_library_cursor,
)
from app.services.artifacts.library import (
    _encode_library_cursor as _encode_library_cursor,
)
from app.services.artifacts.library import (
    _get_owned_artifact as _get_owned_artifact,
)
from app.services.artifacts.library import (
    _get_owned_artifact_with_version as _get_owned_artifact_with_version,
)
from app.services.artifacts.library import (
    _normalize_cursor_datetime as _normalize_cursor_datetime,
)
from app.services.artifacts.recorder import (
    ARTIFACT_SOURCE_TOOL_NAMES,
    ArtifactDelta,
    ArtifactDeltaRecorder,
    ArtifactFileState,
    ArtifactRuntimeContext,
    ArtifactSnapshot,
    diff_snapshots,
    finalize_artifacts_for_run,
    ingest_changed_files,
    link_artifacts_to_messages,
    snapshot_output_dir,
)
from app.services.artifacts.recorder import (
    _state_matches_stat as _state_matches_stat,
)
from app.services.artifacts.recorder import (
    _states_have_same_metadata as _states_have_same_metadata,
)
from app.services.artifacts.summary import (
    FileEventOperation,
    get_artifact_summary,
    get_conversation_artifact_summary,
    is_text_preview_artifact,
)
from app.services.artifacts.summary import (
    _current_version as _current_version,
)
from app.services.artifacts.summary import (
    _current_versions_for_artifacts as _current_versions_for_artifacts,
)
from app.services.artifacts.summary import (
    _file_event_payload as _file_event_payload,
)
from app.services.artifacts.summary import (
    _summaries_from_artifacts as _summaries_from_artifacts,
)
from app.services.artifacts.summary import (
    _summary_from_artifact as _summary_from_artifact,
)
from app.services.artifacts.summary import (
    _summary_from_artifact_with_version as _summary_from_artifact_with_version,
)

__all__ = [
    "ARTIFACT_SOURCE_TOOL_NAMES",
    "LIBRARY_CURSOR_SEPARATOR",
    "ArtifactDelta",
    "ArtifactDeltaRecorder",
    "ArtifactFileState",
    "ArtifactNotFoundError",
    "ArtifactRuntimeContext",
    "ArtifactSnapshot",
    "FileEventOperation",
    "delete_artifact",
    "diff_snapshots",
    "finalize_artifacts_for_run",
    "get_artifact_download_path",
    "get_artifact_summary",
    "get_conversation_artifact_summary",
    "get_library_stats",
    "ingest_changed_files",
    "is_text_preview_artifact",
    "link_artifacts_to_messages",
    "list_conversation_artifacts",
    "list_conversation_artifacts_by_message_id",
    "list_library_artifacts",
    "list_recent_artifacts",
    "read_artifact_text_content",
    "record_artifact_download",
    "record_artifact_opened",
    "set_artifact_favorite",
    "snapshot_output_dir",
]
