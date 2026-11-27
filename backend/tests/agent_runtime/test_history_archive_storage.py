from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.agent_runtime import history_archive
from app.agent_runtime.offload_protocol_projection import project_offload_reference
from app.agent_runtime.offload_storage import (
    OffloadIdentity,
    OffloadSecurityError,
    ScopedOffloadBackend,
    delete_conversation_offloads,
)

_ROOT_ACTOR = UUID("11111111-1111-4111-8111-111111111111")
_CHILD_ACTOR = UUID("22222222-2222-4222-8222-222222222222")


def _backend(
    root: Path,
    *,
    agent_owner: str = "owner",
    conversation_owner: str = "owner",
    conversation: str = "thread",
    run: str = "run",
) -> ScopedOffloadBackend:
    return ScopedOffloadBackend(
        root,
        OffloadIdentity(owner_id=agent_owner, conversation_id=conversation, run_id=run),
        _ROOT_ACTOR,
        archive_owner_id=conversation_owner,
    )


def _encoded(messages: Sequence[BaseMessage]) -> bytes:
    return b"".join((message.model_dump_json() + "\n").encode() for message in messages)


def _large_batch() -> list[ToolMessage]:
    # Distinct tool results cross chunk boundaries, including a multibyte code point.
    return [
        ToolMessage(
            content=(f"업체 {i}의 가격·자료출처 🔍\n" * 8000) + f"결론 {i}",
            id=f"result-{i}",
            tool_call_id=f"call-{i}",
        )
        for i in range(5)
    ]


def test_checkpoint_forks_and_retries_recover_the_exact_original_bytes(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    original = HumanMessage(content="지난달 가격을 비교해 줘요.", id="h-0")
    first = backend.archive_compaction("checkpoint-0", [original])
    assert re.fullmatch(r"archive_[a-f0-9]{32}\.md", first.rsplit("/", 1)[1])
    assert first.startswith(f"{backend.artifacts_root}/conversation_history/")
    projection = project_offload_reference(first, roots=[tmp_path])
    assert projection is not None
    assert projection == backend.project_reference(first)
    assert re.fullmatch(r"history_[0-9a-f]{24}", projection.logical_id)

    resumed = _backend(tmp_path, run="different-run")
    branch_a = AIMessage(
        content="서울·부산",
        id="a-1",
        additional_kwargs={"test_metadata": {"untrimmed": "전체 내용"}},
    )
    branch_b = ToolMessage(content="로그\n둘째 줄", id="t-1", tool_call_id="search")
    ref_a = resumed.archive_compaction("checkpoint-a", [branch_a], previous_file_path=first)
    ref_b = resumed.archive_compaction("checkpoint-b", [branch_b], previous_file_path=first)

    assert first != ref_a != ref_b
    assert backend.archive_compaction("checkpoint-a", [branch_a], previous_file_path=first) == ref_a
    for reference, expected in (
        (first, [original]),
        (ref_a, [original, branch_a]),
        (ref_b, [original, branch_b]),
    ):
        assert b"".join(resumed.iter_archive_bytes(reference)) == _encoded(expected)
    rendered = resumed.download_files([ref_a])[0]
    assert rendered.content is not None
    assert b"test_metadata" not in rendered.content


def test_chunk_failure_publishes_no_manifest_and_retry_reuses_completed_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages = _large_batch()
    reference = _backend(tmp_path / "control").archive_compaction("cp", messages)
    backend = _backend(tmp_path / "candidate")
    write = history_archive.atomic_write_new_or_equal
    parts: list[Path] = []

    def fail_third(path: Path, content: bytes) -> None:
        parts.append(path)
        if len(parts) == 3:
            raise OSError("simulated failed write")
        write(path, content)

    with monkeypatch.context() as patch:
        patch.setattr(history_archive, "atomic_write_new_or_equal", fail_third)
        with pytest.raises(OSError, match="simulated failed write"):
            backend.archive_compaction("cp", messages)

    assert len(parts) == 3
    assert not parts[2].exists()
    assert backend.download_files([reference])[0].error
    with pytest.raises((OffloadSecurityError, FileNotFoundError)):
        b"".join(backend.iter_archive_bytes(reference))
    before = [(part, part.stat().st_ino, part.stat().st_mtime_ns) for part in parts[:2]]

    assert backend.archive_compaction("cp", messages) == reference
    assert [(part, part.stat().st_ino, part.stat().st_mtime_ns) for part in parts[:2]] == before
    chunks = list(backend.iter_archive_bytes(reference))
    assert len(chunks) >= 3
    assert all(0 < len(chunk) <= history_archive.CHUNK_BYTES for chunk in chunks)
    assert hashlib.sha256(b"".join(chunks)).digest() == hashlib.sha256(_encoded(messages)).digest()


def test_concurrent_retry_publishes_the_same_complete_snapshot(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    barrier = Barrier(2)
    messages = [HumanMessage(content="동시 저장 확인", id="m")]

    def archive() -> str:
        barrier.wait(timeout=10)
        return backend.archive_compaction("same-checkpoint", messages)

    with ThreadPoolExecutor(max_workers=2) as workers:
        first, second = (workers.submit(archive) for _ in range(2))
        ref_a, ref_b = first.result(timeout=15), second.result(timeout=15)

    assert ref_a == ref_b
    assert b"".join(backend.iter_archive_bytes(ref_a)) == _encoded(messages)


def test_legacy_prefix_is_frozen_and_archive_is_read_only(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    legacy = f"{backend.artifacts_root}/conversation_history/session_{'a' * 32}.md"
    assert backend.write(legacy, "옛 파일의 원문\n").error is None
    message = AIMessage(content="새 체크포인트 내용", id="a-1")
    archive = backend.archive_compaction("cp", [message], previous_file_path=legacy)
    assert backend.edit(legacy, "옛 파일의 원문", "나중에 수정한 원문").error is None
    assert backend.write(archive, "replace").error
    assert backend.edit(archive, "내용", "replace").error
    assert backend.upload_files([(archive, b"new data")])[0].error
    assert backend.delete(archive).error

    rendered = backend.read(archive)
    assert rendered.error is None and rendered.file_data is not None
    assert "옛 파일의 원문" in rendered.file_data["content"]
    assert "새 체크포인트 내용" in rendered.file_data["content"]
    download = backend.download_files([archive])[0]
    assert download.error is None and download.content is not None
    assert "나중에 수정한 원문" not in download.content.decode()
    assert b"".join(backend.iter_archive_bytes(archive)) == _encoded([message])


def test_wrong_scope_cannot_read_or_link_to_archive(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    archive = backend.archive_compaction("cp", [HumanMessage(content="내 대화", id="m")])
    foreign_backends = [
        backend.for_actor(_CHILD_ACTOR),
        _backend(tmp_path, conversation="other-thread"),
        _backend(tmp_path, agent_owner="other-owner", conversation_owner="other-owner"),
    ]
    for other in foreign_backends:
        assert other.download_files([archive])[0].error
        with pytest.raises((OffloadSecurityError, FileNotFoundError)):
            b"".join(other.iter_archive_bytes(archive))
        with pytest.raises((OffloadSecurityError, FileNotFoundError)):
            other.archive_compaction(
                "new-cp", [AIMessage(content="후속")], previous_file_path=archive
            )
        # Knowing the leaf cannot substitute for permission on the physical actor directory.
        forged = archive.replace(backend.artifacts_root, other.artifacts_root)
        assert other.read(forged).error


def test_same_length_blob_corruption_does_not_return_original_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _backend(tmp_path)
    message = HumanMessage(content="비용 계산의 근거", id="m")
    expected = _encoded([message])
    original_write = history_archive.atomic_write_new_or_equal
    blobs: list[Path] = []

    def capture_chunk(path: Path, content: bytes) -> None:
        original_write(path, content)
        if content == expected:
            blobs.append(path)

    monkeypatch.setattr(history_archive, "atomic_write_new_or_equal", capture_chunk)
    archive = backend.archive_compaction("cp", [message])
    (blob,) = blobs
    blob.write_bytes(expected[:-2] + b"!\n")

    with pytest.raises(OffloadSecurityError):
        b"".join(backend.iter_archive_bytes(archive))
    assert backend.download_files([archive])[0].error


def test_shared_agent_writes_follow_the_conversation_owners_deletion_fence(tmp_path: Path) -> None:
    owner = "conversation-owner"
    shared = "shared-agent-owner"
    own = _backend(tmp_path, agent_owner=shared, conversation_owner=owner, conversation="thread")
    sibling = _backend(
        tmp_path, agent_owner=shared, conversation_owner=shared, conversation="other"
    )
    message = HumanMessage(content="삭제 시 보관 파일도 함께 삭제", id="m")
    archive = own.archive_compaction("cp", [message])
    other = sibling.archive_compaction("cp", [message])

    delete_conversation_offloads(tmp_path, owner_id=owner, conversation_id="thread")
    assert own.read(archive).error
    with pytest.raises(OffloadSecurityError):
        own.archive_compaction("after-delete", [message])
    assert b"".join(sibling.iter_archive_bytes(other)) == _encoded([message])


def test_rollback_reuses_verifiable_prefix_and_later_legacy_turns_follow_deletion(
    tmp_path: Path,
) -> None:
    from deepagents.middleware.summarization import create_summarization_middleware
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from app.agent_runtime.offload_storage_io import read_rollback_history
    from app.agent_runtime.offload_storage_types import rollback_session_id, token

    owner, agent_owner = "member", "agent-author"
    backend = _backend(tmp_path, agent_owner=agent_owner, conversation_owner=owner)
    prior = backend.archive_compaction("a", [HumanMessage(content="첫 번째 근거", id="first")])
    restored = AIMessage(content="두 번째 근거", id="latest")
    session = rollback_session_id(prior, "branch-b")
    legacy = backend.archive_checkpoint_to_legacy(prior, session, [restored])
    leaf = legacy.rsplit("/", 1)[1]
    assert leaf == f"{session}.md"
    resumed = _backend(tmp_path, agent_owner=agent_owner, conversation_owner=owner, run="later-run")

    native = create_summarization_middleware(FakeListChatModel(responses=["요약"]), resumed)
    appended = HumanMessage(content="세 번째 근거", id="new")
    payload = native._aoffload_to_backend(resumed, [appended], session)  # noqa: SLF001
    assert asyncio.run(payload) == legacy
    assert resumed.archive_checkpoint_to_legacy(prior, session, [restored]) == legacy

    content = read_rollback_history(
        tmp_path, owner_id=owner, conversation_id="thread", actor_id=_ROOT_ACTOR, leaf=leaf
    )
    assert resumed.download_files([legacy])[0].content == content
    for snippet in ("첫 번째 근거", "두 번째 근거", "세 번째 근거"):
        assert content.decode().count(snippet) == 1
    with pytest.raises(OffloadSecurityError):
        read_rollback_history(
            tmp_path,
            owner_id=agent_owner,
            conversation_id="thread",
            actor_id=_ROOT_ACTOR,
            leaf=leaf,
        )
    physical = list(tmp_path.rglob(token("history-path", leaf)))
    assert len(physical) == 1 and physical[0].stat().st_nlink == 1

    other_session = rollback_session_id(prior, "branch-c")
    branch = resumed.archive_checkpoint_to_legacy(
        prior, other_session, [AIMessage(content="다른 가지")]
    )
    assert branch != legacy
    other = resumed.download_files([branch])[0]
    assert other.content is not None and "다른 가지" in other.content.decode()
    assert "세 번째 근거" not in other.content.decode()
    with pytest.raises(OffloadSecurityError):
        resumed.archive_checkpoint_to_legacy(prior, session, [AIMessage(content="충돌하는 재시도")])
    assert resumed.download_files([legacy])[0].content == content

    delete_conversation_offloads(tmp_path, owner_id=owner, conversation_id="thread")
    assert not physical[0].exists()
    assert resumed.download_files([legacy])[0].error
    with pytest.raises(OffloadSecurityError, match="deleted"):
        resumed.archive_checkpoint_to_legacy(prior, session, [restored])
    with pytest.raises(OffloadSecurityError, match="deleted"):
        resumed.write(legacy, "재시작된 워커")


def test_interrupted_rollback_is_invisible_and_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agent_runtime.offload_storage_backends import HistoryBackend
    from app.agent_runtime.offload_storage_types import rollback_session_id

    backend = _backend(tmp_path)
    prior = backend.archive_compaction("a", [HumanMessage(content="원문", id="m")])
    session = rollback_session_id(prior, "after-switch")
    legacy = f"{backend.artifacts_root}/conversation_history/{session}.md"
    expected = AIMessage(content="그 이후")

    def fail_mid_render(self: HistoryBackend, leaf: str) -> Iterator[bytes]:
        yield b"incomplete prefix"
        raise OSError("interrupted rollback")

    with monkeypatch.context() as patch:
        patch.setattr(HistoryBackend, "archive_markdown", fail_mid_render)
        with pytest.raises(OSError, match="interrupted rollback"):
            backend.archive_checkpoint_to_legacy(prior, session, [expected])

    assert backend.read(legacy).error
    assert not list(tmp_path.rglob(".rollback.*.tmp"))
    assert backend.archive_checkpoint_to_legacy(prior, session, [expected]) == legacy
    restored = backend.download_files([legacy])[0]
    assert restored.content and "원문" in restored.content.decode()
    assert b"incomplete prefix" not in restored.content


def _historical_import_and_rollback(
    tmp_path: Path,
) -> tuple[ScopedOffloadBackend, str, str]:
    from app.agent_runtime.offload_storage_types import rollback_session_id

    first = _backend(tmp_path, agent_owner="author", conversation_owner="member")
    archive = first.archive_compaction(
        "before", [HumanMessage(content="새 형식 원문", id="initial")]
    )
    session = rollback_session_id(archive, "next-checkpoint")
    leaf = f"{session}.md"
    source = tmp_path / "conversation_history" / leaf
    source.parent.mkdir()
    source.write_text("이전에 가져온 원문", encoding="utf-8")
    verified = ScopedOffloadBackend(
        tmp_path,
        OffloadIdentity("author", "thread", "run", verified_legacy_sessions=frozenset({leaf})),
        _ROOT_ACTOR,
        archive_owner_id="member",
    )
    return verified, archive, session


def test_preexisting_import_matching_reserved_session_stays_readable_until_deletion(
    tmp_path: Path,
) -> None:
    from app.agent_runtime.offload_storage_io import read_rollback_history
    from app.agent_runtime.offload_storage_types import LegacyHistoryKind

    backend, archive, session = _historical_import_and_rollback(tmp_path)
    leaf = f"{session}.md"
    imported = backend.migrate_legacy_history(LegacyHistoryKind.SESSION, source_filename=leaf)
    assert backend.edit(imported.virtual_path, "원문", "수정한 원문").error is None
    expected = "이전에 가져온 수정한 원문".encode()
    assert backend.download_files([imported.virtual_path])[0].content == expected

    def read_evidence() -> bytes:
        return read_rollback_history(
            tmp_path,
            owner_id="member",
            legacy_owner_id="author",
            conversation_id="thread",
            actor_id=_ROOT_ACTOR,
            leaf=leaf,
        )

    assert read_evidence() == expected
    with pytest.raises(OffloadSecurityError, match="conflicts with imported"):
        backend.archive_checkpoint_to_legacy(
            archive, session, [AIMessage(content="덮어쓸 수 없음")]
        )
    assert read_evidence() == expected

    delete_conversation_offloads(tmp_path, owner_id="member", conversation_id="thread")
    assert backend.read(imported.virtual_path).error
    with pytest.raises(OffloadSecurityError, match="deleted"):
        read_evidence()
    with pytest.raises(OffloadSecurityError, match="deleted"):
        backend.write(imported.virtual_path, "되살릴 수 없음")


def test_import_and_rollback_cannot_both_claim_same_shared_history(tmp_path: Path) -> None:
    from app.agent_runtime.offload_storage_types import LegacyHistoryKind

    backend, archive, session = _historical_import_and_rollback(tmp_path)
    leaf = f"{session}.md"
    barrier = Barrier(2)

    def import_or_rollback(importing: bool) -> bytes:
        barrier.wait(timeout=10)
        if importing:
            backend.migrate_legacy_history(LegacyHistoryKind.SESSION, source_filename=leaf)
            return "이전에 가져온 원문".encode()
        backend.archive_checkpoint_to_legacy(archive, session, [AIMessage(content="이후 요약")])
        return "이후 요약".encode()

    with ThreadPoolExecutor(max_workers=2) as workers:
        attempts = [workers.submit(import_or_rollback, mode) for mode in (False, True)]
        failures = [future for future in attempts if future.exception(timeout=15) is not None]
        assert len(failures) == 1 and isinstance(failures[0].exception(), OffloadSecurityError)
        winner = next(future.result() for future in attempts if future not in failures)
    ref = f"{backend.artifacts_root}/conversation_history/{leaf}"
    actual = backend.download_files([ref])[0].content
    assert actual and winner in actual
    assert ("이후 요약".encode() in actual) != ("이전에 가져온 원문".encode() in actual)


def test_invalid_rollback_file_cannot_fall_back_to_verified_import(tmp_path: Path) -> None:
    from app.agent_runtime.offload_storage_io import read_rollback_history, rollback_history_path
    from app.agent_runtime.offload_storage_types import (
        LegacyHistoryKind,
        OffloadMutationScope,
        token,
    )

    backend, _, session = _historical_import_and_rollback(tmp_path)
    leaf = f"{session}.md"
    imported = backend.migrate_legacy_history(LegacyHistoryKind.SESSION, source_filename=leaf)
    scope = OffloadMutationScope(
        tmp_path / ".moldy-internal" / "offload",
        token("owner", "member"),
        token("conversation", "thread"),
    )
    malformed = rollback_history_path(scope, token("actor", str(_ROOT_ACTOR)), leaf)
    malformed.parent.mkdir(parents=True, exist_ok=True)
    malformed.symlink_to(tmp_path / "conversation_history" / leaf)
    assert backend.download_files([imported.virtual_path])[0].error
    with pytest.raises((OSError, OffloadSecurityError)):
        read_rollback_history(
            tmp_path,
            owner_id="member",
            legacy_owner_id="author",
            conversation_id="thread",
            actor_id=_ROOT_ACTOR,
            leaf=leaf,
        )
