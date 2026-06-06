from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import event, select, update

from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.services.artifact_service import ArtifactDeltaRecorder, ArtifactRuntimeContext
from app.services.artifact_storage import LocalArtifactStorageBackend
from tests.artifact_test_helpers import seed_artifact_conversation
from tests.conftest import TEST_USER_ID, TestSession, engine


@pytest.mark.asyncio
async def test_recorder_ingests_deepagents_write_file_outputs(tmp_path: Path) -> None:
    conv_id, agent_id = await seed_artifact_conversation()
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    storage = LocalArtifactStorageBackend(tmp_path / "artifacts")
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conv_id,
            user_id=TEST_USER_ID,
            agent_id=agent_id,
            assistant_msg_id="run-write-file",
            output_dir=output_dir,
        ),
        storage=storage,
    )

    await recorder.prepare()
    (output_dir / "today_diary.md").write_text("# 오늘 하루 일기\n", encoding="utf-8")

    events = await recorder.collect_after_tool_result(
        tool_name="write_file",
        tool_call_id="call-write-file",
    )

    assert [event["op"] for event in events] == ["created"]
    assert events[0]["path"] == "today_diary.md"
    async with TestSession() as db:
        artifact = (
            await db.execute(
                select(ConversationArtifact).where(
                    ConversationArtifact.assistant_msg_id == "run-write-file"
                )
            )
        ).scalar_one()
    assert artifact.source_tool_name == "write_file"
    assert artifact.tool_call_id == "call-write-file"


@pytest.mark.asyncio
async def test_recorder_ingests_same_content_rewrite_for_new_run(tmp_path: Path) -> None:
    conv_id, agent_id = await seed_artifact_conversation()
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    report = output_dir / "report.md"
    report.write_text("same content", encoding="utf-8")
    storage = LocalArtifactStorageBackend(tmp_path / "artifacts")
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conv_id,
            user_id=TEST_USER_ID,
            agent_id=agent_id,
            assistant_msg_id="run-same-content",
            output_dir=output_dir,
        ),
        storage=storage,
    )

    await recorder.prepare()
    previous_mtime = report.stat().st_mtime_ns
    report.write_text("same content", encoding="utf-8")
    os.utime(report, ns=(previous_mtime + 1_000_000_000, previous_mtime + 1_000_000_000))

    events = await recorder.collect_after_tool_result(
        tool_name="execute_in_skill",
        tool_call_id="call-same-content",
    )

    assert [event["op"] for event in events] == ["created"]
    assert events[0]["path"] == "report.md"


@pytest.mark.asyncio
async def test_snapshot_hashes_files_off_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import artifact_service

    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    (output_dir / "report.md").write_text("hello", encoding="utf-8")
    calls: list[object] = []

    async def fake_to_thread(func, /, *args, **kwargs):
        calls.append(func)
        return func(*args, **kwargs)

    monkeypatch.setattr(artifact_service.asyncio, "to_thread", fake_to_thread)

    snapshot = await artifact_service.snapshot_output_dir(output_dir)

    assert "report.md" in snapshot.files
    assert calls, "snapshot hashing should be offloaded from the event loop"


@pytest.mark.asyncio
async def test_recorder_hashes_only_new_or_changed_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import artifact_service

    conv_id, agent_id = await seed_artifact_conversation()
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    (output_dir / "old.md").write_text("unchanged", encoding="utf-8")
    storage = LocalArtifactStorageBackend(tmp_path / "artifacts")
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conv_id,
            user_id=TEST_USER_ID,
            agent_id=agent_id,
            assistant_msg_id="run-hash-delta",
            output_dir=output_dir,
        ),
        storage=storage,
    )
    hashed_paths: list[str] = []

    async def fake_sha256_file(path: Path) -> str:
        hashed_paths.append(path.name)
        return "b" * 64

    monkeypatch.setattr(artifact_service, "_sha256_file", fake_sha256_file)
    await recorder.prepare()
    (output_dir / "new.md").write_text("new", encoding="utf-8")

    events = await recorder.collect_after_tool_result(
        tool_name="execute_in_skill",
        tool_call_id="call-hash-delta",
    )

    assert hashed_paths == ["new.md"]
    assert [event["path"] for event in events] == ["new.md"]


@pytest.mark.asyncio
async def test_recorder_detects_same_size_rewrite_with_preserved_mtime(
    tmp_path: Path,
) -> None:
    conv_id, agent_id = await seed_artifact_conversation()
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    storage = LocalArtifactStorageBackend(tmp_path / "artifacts")
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conv_id,
            user_id=TEST_USER_ID,
            agent_id=agent_id,
            assistant_msg_id="run-preserved-mtime",
            output_dir=output_dir,
        ),
        storage=storage,
    )

    await recorder.prepare()
    report = output_dir / "report.md"
    report.write_text("alpha", encoding="utf-8")
    created = await recorder.collect_after_tool_result(
        tool_name="execute_in_skill",
        tool_call_id="call-preserved-mtime",
    )
    assert [event["op"] for event in created] == ["created"]

    previous_stat = report.stat()
    await asyncio.sleep(0.001)
    report.write_text("bravo", encoding="utf-8")
    os.utime(report, ns=(previous_stat.st_mtime_ns, previous_stat.st_mtime_ns))

    updated = await recorder.collect_after_tool_result(
        tool_name="execute_in_skill",
        tool_call_id="call-preserved-mtime",
    )

    assert [event["op"] for event in updated] == ["updated"]
    assert updated[0]["version_number"] == 2
    assert updated[0]["sha256"] != created[0]["sha256"]


@pytest.mark.asyncio
async def test_recorder_ingests_created_and_updated_file(tmp_path: Path) -> None:
    conv_id, agent_id = await seed_artifact_conversation()
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    storage = LocalArtifactStorageBackend(tmp_path / "artifacts")
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conv_id,
            user_id=TEST_USER_ID,
            agent_id=agent_id,
            assistant_msg_id="run-1",
            output_dir=output_dir,
        ),
        storage=storage,
    )

    await recorder.prepare()
    report = output_dir / "report.md"
    report.write_text("v1", encoding="utf-8")
    created = await recorder.collect_after_tool_result(
        tool_name="execute_in_skill",
        tool_call_id="call-1",
    )

    assert [event["op"] for event in created] == ["created"]
    assert created[0]["artifact_kind"] == "markdown"
    artifact_id = created[0]["id"]

    report.write_text("v2", encoding="utf-8")
    updated = await recorder.collect_after_tool_result(
        tool_name="execute_in_skill",
        tool_call_id="call-1",
    )

    assert [event["op"] for event in updated] == ["updated"]
    assert updated[0]["id"] == artifact_id
    assert updated[0]["version_number"] == 2

    async with TestSession() as db:
        artifacts = (await db.execute(select(ConversationArtifact))).scalars().all()
        versions = (await db.execute(select(ArtifactVersion))).scalars().all()
        assert len(artifacts) == 1
        assert len(versions) == 2
        assert artifacts[0].agent_id == agent_id
        assert artifacts[0].logical_path == "report.md"
        assert artifacts[0].artifact_kind == "markdown"
    assert artifacts[0].current_version_id == versions[-1].id


@pytest.mark.asyncio
async def test_recorder_marks_deleted_file_and_emits_event(tmp_path: Path) -> None:
    conv_id, agent_id = await seed_artifact_conversation()
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    storage = LocalArtifactStorageBackend(tmp_path / "artifacts")
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conv_id,
            user_id=TEST_USER_ID,
            agent_id=agent_id,
            assistant_msg_id="run-delete",
            output_dir=output_dir,
        ),
        storage=storage,
    )

    await recorder.prepare()
    report = output_dir / "report.md"
    report.write_text("v1", encoding="utf-8")
    created = await recorder.collect_after_tool_result(
        tool_name="execute_in_skill",
        tool_call_id="call-delete",
    )
    artifact_id = created[0]["id"]

    report.unlink()
    deleted = await recorder.collect_after_tool_result(
        tool_name="execute_in_skill",
        tool_call_id="call-delete",
    )

    assert [event["op"] for event in deleted] == ["deleted"]
    assert deleted[0]["id"] == artifact_id
    assert deleted[0]["status"] == "deleted"

    async with TestSession() as db:
        artifacts = (await db.execute(select(ConversationArtifact))).scalars().all()
        versions = (await db.execute(select(ArtifactVersion))).scalars().all()
        assert len(artifacts) == 1
        assert artifacts[0].status == "deleted"
        assert len(versions) == 1


@pytest.mark.asyncio
async def test_recorder_delete_does_not_mark_previous_run_artifact_deleted(
    tmp_path: Path,
) -> None:
    conv_id, agent_id = await seed_artifact_conversation()
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    storage = LocalArtifactStorageBackend(tmp_path / "artifacts")

    previous_recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conv_id,
            user_id=TEST_USER_ID,
            agent_id=agent_id,
            assistant_msg_id="run-previous",
            output_dir=output_dir,
        ),
        storage=storage,
    )
    await previous_recorder.prepare()
    report = output_dir / "report.md"
    report.write_text("previous", encoding="utf-8")
    previous_events = await previous_recorder.collect_after_tool_result(
        tool_name="execute_in_skill",
        tool_call_id="call-previous",
    )
    previous_artifact_id = previous_events[0]["id"]

    current_recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conv_id,
            user_id=TEST_USER_ID,
            agent_id=agent_id,
            assistant_msg_id="run-current",
            output_dir=output_dir,
        ),
        storage=storage,
    )
    await current_recorder.prepare()
    report.unlink()
    deleted = await current_recorder.collect_after_tool_result(
        tool_name="execute_in_skill",
        tool_call_id="call-current",
    )

    assert deleted == []

    async with TestSession() as db:
        artifacts = (await db.execute(select(ConversationArtifact))).scalars().all()
        assert len(artifacts) == 1
        assert str(artifacts[0].id) == previous_artifact_id
        assert artifacts[0].assistant_msg_id == "run-previous"
        assert artifacts[0].status == "ready"


@pytest.mark.asyncio
async def test_library_filters_favorite_and_stats(tmp_path: Path) -> None:
    from app.services import artifact_service

    conv_id, agent_id = await seed_artifact_conversation()
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conv_id,
            user_id=TEST_USER_ID,
            agent_id=agent_id,
            assistant_msg_id="run-library-1",
            output_dir=output_dir,
        ),
        storage=LocalArtifactStorageBackend(tmp_path / "artifacts"),
    )
    await recorder.prepare()
    (output_dir / "chart.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    events = await recorder.collect_after_tool_result(
        tool_name="execute_in_skill",
        tool_call_id="call-1",
    )
    artifact_id = events[0]["id"]

    async with TestSession() as db:
        page = await artifact_service.list_library_artifacts(
            db,
            user_id=TEST_USER_ID,
            q="chart",
            agent_id=agent_id,
            conversation_id=conv_id,
            kind="data",
            favorite=None,
            limit=50,
            cursor=None,
        )
        assert len(page.items) == 1
        favorited = await artifact_service.set_artifact_favorite(
            db,
            user_id=TEST_USER_ID,
            artifact_id=artifact_id,
            is_favorite=True,
        )
        assert favorited.is_favorite is True
        opened = await artifact_service.record_artifact_opened(
            db,
            user_id=TEST_USER_ID,
            artifact_id=artifact_id,
        )
        assert opened.preview_count == 1
        stats = await artifact_service.get_library_stats(db, user_id=TEST_USER_ID)
        assert stats.total_count == 1
        assert stats.favorite_count == 1
        assert stats.by_kind[0].kind == "data"


@pytest.mark.asyncio
async def test_download_path_missing_storage_object_raises_not_found(tmp_path: Path) -> None:
    from app.services import artifact_service

    conv_id, agent_id = await seed_artifact_conversation()
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    storage = LocalArtifactStorageBackend(tmp_path / "artifacts")
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conv_id,
            user_id=TEST_USER_ID,
            agent_id=agent_id,
            assistant_msg_id="run-missing-object",
            output_dir=output_dir,
        ),
        storage=storage,
    )
    await recorder.prepare()
    (output_dir / "report.md").write_text("hello", encoding="utf-8")
    events = await recorder.collect_after_tool_result(
        tool_name="execute_in_skill",
        tool_call_id="call-missing-object",
    )
    artifact_id = events[0]["id"]
    for path in (tmp_path / "artifacts").rglob("*"):
        if path.is_file():
            path.unlink()

    async with TestSession() as db:
        with pytest.raises(artifact_service.ArtifactNotFoundError):
            await artifact_service.get_artifact_download_path(
                db,
                user_id=TEST_USER_ID,
                artifact_id=artifact_id,
                storage=storage,
            )


@pytest.mark.asyncio
async def test_library_cursor_keeps_same_timestamp_rows(tmp_path: Path) -> None:
    from app.services import artifact_service

    conv_id, agent_id = await seed_artifact_conversation()
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conv_id,
            user_id=TEST_USER_ID,
            agent_id=agent_id,
            assistant_msg_id="run-library-cursor",
            output_dir=output_dir,
        ),
        storage=LocalArtifactStorageBackend(tmp_path / "artifacts"),
    )
    await recorder.prepare()
    for name in ("a.md", "b.md", "c.md"):
        (output_dir / name).write_text(name, encoding="utf-8")
    await recorder.collect_after_tool_result(
        tool_name="execute_in_skill",
        tool_call_id="call-cursor",
    )

    same_created_at = datetime(2026, 1, 1, 12, 0, 0)
    async with TestSession() as db:
        await db.execute(
            update(ConversationArtifact)
            .where(ConversationArtifact.conversation_id == conv_id)
            .values(created_at=same_created_at)
        )
        await db.commit()

    async with TestSession() as db:
        first_page = await artifact_service.list_library_artifacts(
            db,
            user_id=TEST_USER_ID,
            q=None,
            agent_id=None,
            conversation_id=conv_id,
            kind=None,
            favorite=None,
            limit=2,
            cursor=None,
        )
        assert first_page.has_more is True
        assert first_page.next_cursor is not None

        second_page = await artifact_service.list_library_artifacts(
            db,
            user_id=TEST_USER_ID,
            q=None,
            agent_id=None,
            conversation_id=conv_id,
            kind=None,
            favorite=None,
            limit=2,
            cursor=first_page.next_cursor,
        )

    paged_items = first_page.items + second_page.items
    assert len(paged_items) == 3
    assert {item.path for item in paged_items} == {"a.md", "b.md", "c.md"}
    assert second_page.has_more is False
    assert second_page.next_cursor is None


@pytest.mark.asyncio
async def test_library_lists_batch_load_summary_metadata(tmp_path: Path) -> None:
    from app.services import artifact_service

    conv_id, agent_id = await seed_artifact_conversation()
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conv_id,
            user_id=TEST_USER_ID,
            agent_id=agent_id,
            assistant_msg_id="run-library-batch",
            output_dir=output_dir,
        ),
        storage=LocalArtifactStorageBackend(tmp_path / "artifacts"),
    )
    await recorder.prepare()
    for name in ("a.md", "b.md", "c.md"):
        (output_dir / name).write_text(name, encoding="utf-8")
    await recorder.collect_after_tool_result(
        tool_name="execute_in_skill",
        tool_call_id="call-library-batch",
    )

    select_statements: list[str] = []

    def count_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            select_statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", count_selects)
    try:
        async with TestSession() as db:
            page = await artifact_service.list_library_artifacts(
                db,
                user_id=TEST_USER_ID,
                q=None,
                agent_id=None,
                conversation_id=conv_id,
                kind=None,
                favorite=None,
                limit=50,
                cursor=None,
            )
            assert len(page.items) == 3
            library_select_count = len(select_statements)

            select_statements.clear()
            recent = await artifact_service.list_recent_artifacts(
                db,
                user_id=TEST_USER_ID,
                limit=8,
            )
            assert len(recent) == 3
            recent_select_count = len(select_statements)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_selects)

    assert library_select_count <= 4
    assert recent_select_count <= 4


@pytest.mark.asyncio
async def test_artifacts_can_be_grouped_by_message_id(tmp_path: Path) -> None:
    from app.services import artifact_service

    conv_id, agent_id = await seed_artifact_conversation()
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conv_id,
            user_id=TEST_USER_ID,
            agent_id=agent_id,
            assistant_msg_id="run-message-link",
            output_dir=output_dir,
        ),
        storage=LocalArtifactStorageBackend(tmp_path / "artifacts"),
    )
    await recorder.prepare()
    (output_dir / "report.md").write_text("hello", encoding="utf-8")
    await recorder.collect_after_tool_result(
        tool_name="execute_in_skill",
        tool_call_id="call-1",
    )

    async with TestSession() as db:
        await artifact_service.link_artifacts_to_messages(
            db,
            conversation_id=conv_id,
            assistant_msg_id="run-message-link",
            linked_message_ids=["message-1"],
        )
        await db.commit()

    async with TestSession() as db:
        grouped = await artifact_service.list_conversation_artifacts_by_message_id(
            db,
            user_id=TEST_USER_ID,
            conversation_id=conv_id,
        )

    assert list(grouped) == ["message-1"]
    assert grouped["message-1"][0].display_name == "report.md"


@pytest.mark.asyncio
async def test_read_artifact_text_content_reads_only_preview_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import artifact_service

    conv_id, agent_id = await seed_artifact_conversation()
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    storage = LocalArtifactStorageBackend(tmp_path / "artifacts")
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conv_id,
            user_id=TEST_USER_ID,
            agent_id=agent_id,
            assistant_msg_id="run-preview-window",
            output_dir=output_dir,
        ),
        storage=storage,
    )
    await recorder.prepare()
    (output_dir / "large.txt").write_text("abcdefghi", encoding="utf-8")
    events = await recorder.collect_after_tool_result(
        tool_name="execute_in_skill",
        tool_call_id="call-preview",
    )

    def fail_full_file_read(_self: Path) -> bytes:
        raise AssertionError("preview should not read the full artifact file")

    monkeypatch.setattr(Path, "read_bytes", fail_full_file_read)

    async with TestSession() as db:
        preview = await artifact_service.read_artifact_text_content(
            db,
            user_id=TEST_USER_ID,
            artifact_id=events[0]["id"],
            storage=storage,
            max_bytes=5,
        )

    assert preview.text == "abcde"
    assert preview.truncated is True
