from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import update

from app.config import settings
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.models.conversation_run import ConversationRun
from app.models.organization import OrganizationMember
from app.schemas.conversation_goal_criteria import CsvTableCriterion, GoalEvidence
from app.services import conversation_goal_evidence
from app.services.artifact_storage import LocalArtifactStorageBackend
from app.services.artifacts.errors import ArtifactNotFoundError
from app.services.conversation_goal_evidence import (
    _check_table,
    inspect_goal_evidence,
    read_goal_evidence_version,
)
from tests.artifact_test_helpers import seed_artifact_conversation
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession

_CONTEXT = unit_context(TEST_USER_ID)
_TABLE_PATH = "결과/비교.csv"
_GOAL_TABLE = {
    "kind": "csv_table",
    "logical_path": _TABLE_PATH,
    "required_columns": ["업체", "국가", "가격", "출처"],
    "unique_by": "업체",
    "minimum_rows": 3,
    "column_equals": {"국가": "대한민국"},
}

_REFERENCE = [
    {"업체": key, "국가": "대한민국", "가격": price, "출처": f"https://example.test/{key}"}
    for key, price in (("A", "150"), ("B", "250"), ("C", "300"))
]


def _csv(rows: list[dict[str, str]]) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=list(_REFERENCE[0]))
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8-sig")


@pytest.mark.parametrize(
    ("field", "override", "reason"),
    [
        ("국가", "미국", "column_mismatch"),
        ("가격", "", "empty_cell"),
        ("업체", "ｂ", "duplicate_key"),
        ("가격", "151", "reference_mismatch"),
        ("출처", "https://example.test/a", "reference_mismatch"),
    ],
)
def test_table_requires_every_row_and_confirmed_value(
    field: str, override: str, reason: str
) -> None:
    rows = [dict(row) for row in _REFERENCE]
    rows[2][field] = override
    criterion = CsvTableCriterion.model_validate({**_GOAL_TABLE, "reference_rows": _REFERENCE})
    assert _check_table(criterion, _csv(rows)) == reason


def test_table_checks_empty_missing_malformed_and_minimum_rows() -> None:
    criterion = CsvTableCriterion.model_validate(_GOAL_TABLE)
    assert _check_table(criterion, b"") == "invalid_header"
    assert _check_table(criterion, "업체,가격\n테스트,100\n".encode()) == "missing_columns"
    assert _check_table(criterion, _csv(_REFERENCE[:2])) == "insufficient_rows"
    assert _check_table(criterion, b"\xff") == "invalid_table"
    assert _check_table(criterion, _csv(list(reversed(_REFERENCE)))) == "verified"


@pytest.mark.parametrize(
    "source",
    [
        "https://catalog.example.test/products/a?edition=KR",
        "https://catalog.example.test/products/A?edition=kr",
        "https://catalog.example.test/products/Ａ?edition=KR",
    ],
)
def test_source_filter_preserves_the_exact_resource_without_reference_rows(source: str) -> None:
    expected = "https://catalog.example.test/products/A?edition=KR"
    criterion = CsvTableCriterion.model_validate(
        {**_GOAL_TABLE, "column_equals": {"국가": "대한민국", "출처": expected}}
    )
    rows = [{**row, "출처": expected} for row in _REFERENCE]
    assert _check_table(criterion, _csv(rows)) == "verified"
    rows[1]["출처"] = source
    assert _check_table(criterion, _csv(rows)) == "column_mismatch"


@pytest.mark.parametrize(
    "change",
    [
        {"logical_path": "../foreign.csv"},
        {"logical_path": "/tmp/report.csv"},
        {"required_columns": ["업체", "국가", "업체"]},
        {"unique_by": "없는 열"},
        {"column_equals": {"다른 열": "x"}},
        {"reference_rows": _REFERENCE[:2]},
        {"reference_rows": [*_REFERENCE[:2], _REFERENCE[0]]},
    ],
)
def test_ambiguous_criteria_are_rejected(change: dict) -> None:
    with pytest.raises(ValidationError):
        CsvTableCriterion.model_validate({**_GOAL_TABLE, **change})


async def _seed_run_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    data: bytes,
    scope: tuple[UUID, UUID] | None = None,
    logical_path: str = _TABLE_PATH,
) -> tuple[UUID, UUID, UUID, UUID]:
    conversation_id, agent_id = scope or await seed_artifact_conversation()
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "store"))
    run_id, artifact_id, version_id = uuid4(), uuid4(), uuid4()
    source = tmp_path / "report.csv"
    source.write_bytes(data)
    stored = await LocalArtifactStorageBackend().put_file(
        conversation_id=conversation_id,
        tenant_id=_CONTEXT.tenant_id,
        artifact_id=artifact_id,
        version_number=1,
        display_name="비교.csv",
        source_path=source,
    )
    checksum = hashlib.sha256(data).hexdigest()
    async with TestSession() as db:
        db.add(
            ConversationRun(
                id=run_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                user_id=TEST_USER_ID,
                org_id=_CONTEXT.org_id,
                tenant_id=_CONTEXT.tenant_id,
                source="chat",
                run_source="CHAT",
                status="completed",
                is_active=False,
            )
        )
        artifact = ConversationArtifact(
            id=artifact_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            user_id=TEST_USER_ID,
            org_id=_CONTEXT.org_id,
            tenant_id=_CONTEXT.tenant_id,
            assistant_msg_id=str(run_id),
            logical_path=logical_path,
            display_name="비교.csv",
            extension="csv",
            mime_type="text/csv",
            artifact_kind="data",
            size_bytes=len(data),
            sha256=checksum,
            current_version_id=version_id,
            status="ready",
        )
        db.add(artifact)
        await db.flush()
        db.add(
            ArtifactVersion(
                id=version_id,
                artifact_id=artifact_id,
                version_number=1,
                object_key=stored.object_key,
                original_filename="비교.csv",
                size_bytes=len(data),
                sha256=checksum,
            )
        )
        await db.commit()
    return conversation_id, run_id, artifact_id, version_id


async def test_evidence_is_bound_to_immutable_verified_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = _csv(_REFERENCE)
    cid, rid, aid, vid = await _seed_run_artifact(tmp_path, monkeypatch, data=data)
    async with TestSession() as db:
        conversation, run = await db.get(Conversation, cid), await db.get(ConversationRun, rid)
        assert conversation and run
        checked = await inspect_goal_evidence(
            db,
            conversation=conversation,
            run=run,
            criteria=[
                {**_GOAL_TABLE, "reference_rows": _REFERENCE},
                {"kind": "nonempty_file", "logical_path": _GOAL_TABLE["logical_path"]},
            ],
        )
        assert checked.all_passed and checked.progress == 2
        for check in checked.checks:
            assert (check.artifact_id, check.version_id, check.version_number) == (aid, vid, 1)
            assert check.sha256 == hashlib.sha256(data).hexdigest()
        await db.execute(
            update(ConversationArtifact)
            .where(ConversationArtifact.id == aid)
            .values(current_version_id=uuid4())
        )
        await db.commit()
        version, content = await read_goal_evidence_version(
            db, context=_CONTEXT, conversation_id=cid, artifact_id=aid, version_id=vid
        )
        assert version.id == vid and content == data
        for wrong in (
            {"conversation_id": uuid4()},
            {"artifact_id": uuid4()},
            {"version_id": uuid4()},
        ):
            params = {"conversation_id": cid, "artifact_id": aid, "version_id": vid, **wrong}
            with pytest.raises(ArtifactNotFoundError):
                await read_goal_evidence_version(db, context=_CONTEXT, **params)
        unavailable = await inspect_goal_evidence(
            db, conversation=conversation, run=run, criteria=[_GOAL_TABLE]
        )
        assert not unavailable.all_passed and unavailable.checks[0].reason == "artifact_missing"


async def test_ineligible_artifacts_and_unsupported_conditions_never_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, rid, aid, vid = await _seed_run_artifact(tmp_path, monkeypatch, data=_csv(_REFERENCE))
    async with TestSession() as db:
        conversation, run = await db.get(Conversation, cid), await db.get(ConversationRun, rid)
        assert conversation and run
        for criteria in (
            [],
            [{"kind": "manual", "description": "현지에서 가격을 확인한다"}],
            [{"kind": "unverified"}],
        ):
            evaluation = await inspect_goal_evidence(
                db, conversation=conversation, run=run, criteria=criteria
            )
            assert not evaluation.all_passed and evaluation.progress == 0
        foreign, _ = await seed_artifact_conversation()
        await db.execute(
            update(ConversationArtifact)
            .where(ConversationArtifact.id == aid)
            .values(conversation_id=foreign)
        )
        await db.commit()
        checked = await inspect_goal_evidence(
            db, conversation=conversation, run=run, criteria=[_GOAL_TABLE]
        )
        assert checked.checks[0].reason == "artifact_missing" and not checked.all_passed
        with pytest.raises(ArtifactNotFoundError):
            await read_goal_evidence_version(
                db, context=_CONTEXT, conversation_id=cid, artifact_id=aid, version_id=vid
            )
        await db.execute(
            update(ConversationArtifact)
            .where(ConversationArtifact.id == aid)
            .values(conversation_id=cid, assistant_msg_id=str(uuid4()))
        )
        await db.commit()
        other_run = await inspect_goal_evidence(
            db, conversation=conversation, run=run, criteria=[_GOAL_TABLE]
        )
        assert other_run.checks[0].version_id is None and not other_run.all_passed


@pytest.mark.parametrize("replacement", [b"", b"012", b"01234", b"abcde", None])
async def test_missing_or_changed_bytes_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: bytes | None
) -> None:
    cid, rid, aid, vid = await _seed_run_artifact(tmp_path, monkeypatch, data=b"12345")
    async with TestSession() as db:
        conversation, run = await db.get(Conversation, cid), await db.get(ConversationRun, rid)
        version = await db.get(ArtifactVersion, vid)
        assert conversation and run and version
        path = await LocalArtifactStorageBackend().local_path(
            object_key=version.object_key, tenant_id=_CONTEXT.tenant_id
        )
        if replacement is None:
            path.unlink()
        else:
            path.write_bytes(replacement)
        evidence = await inspect_goal_evidence(
            db,
            conversation=conversation,
            run=run,
            criteria=[{"kind": "nonempty_file", "logical_path": _GOAL_TABLE["logical_path"]}],
        )
        assert not evidence.all_passed and evidence.checks[0].reason == "unreadable_version"
        with pytest.raises(ArtifactNotFoundError):
            await read_goal_evidence_version(
                db, context=_CONTEXT, conversation_id=cid, artifact_id=aid, version_id=vid
            )


async def test_verification_requires_terminal_run_and_current_membership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, rid, aid, vid = await _seed_run_artifact(tmp_path, monkeypatch, data=_csv(_REFERENCE))
    async with TestSession() as db:
        conversation, run = await db.get(Conversation, cid), await db.get(ConversationRun, rid)
        assert conversation and run
        run.status, run.is_active = "running", True
        await db.commit()
        result = await inspect_goal_evidence(
            db, conversation=conversation, run=run, criteria=[_GOAL_TABLE]
        )
        assert not result.all_passed and result.checks[0].reason == "run_unavailable"
        await db.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.user_id == TEST_USER_ID,
                OrganizationMember.org_id == _CONTEXT.org_id,
            )
            .values(status="suspended")
        )
        await db.commit()
        with pytest.raises(AppError):
            await inspect_goal_evidence(
                db, conversation=conversation, run=run, criteria=[_GOAL_TABLE]
            )
        with pytest.raises(AppError):
            await read_goal_evidence_version(
                db, context=_CONTEXT, conversation_id=cid, artifact_id=aid, version_id=vid
            )


async def test_evidence_rejects_other_artifacts_object_even_with_matching_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = _csv(_REFERENCE)
    cid, rid, aid, vid = await _seed_run_artifact(tmp_path, monkeypatch, data=data)
    _, _, _, foreign_vid = await _seed_run_artifact(tmp_path, monkeypatch, data=data)
    async with TestSession() as db:
        foreign_version = await db.get(ArtifactVersion, foreign_vid)
        version = await db.get(ArtifactVersion, vid)
        conversation, run = await db.get(Conversation, cid), await db.get(ConversationRun, rid)
        assert foreign_version and version and conversation and run
        version.object_key = foreign_version.object_key
        await db.commit()
        checked = await inspect_goal_evidence(
            db, conversation=conversation, run=run, criteria=[_GOAL_TABLE]
        )
        assert not checked.all_passed and checked.checks[0].reason == "unreadable_version"
        with pytest.raises(ArtifactNotFoundError):
            await read_goal_evidence_version(
                db, context=_CONTEXT, conversation_id=cid, artifact_id=aid, version_id=vid
            )


async def test_table_never_accepts_only_a_truncated_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = _csv(_REFERENCE)
    cid, rid, _, _ = await _seed_run_artifact(tmp_path, monkeypatch, data=data)
    monkeypatch.setattr(settings, "artifact_preview_max_text_bytes", len(data) - 1)
    async with TestSession() as db:
        conversation, run = await db.get(Conversation, cid), await db.get(ConversationRun, rid)
        assert conversation and run
        checked = await inspect_goal_evidence(
            db, conversation=conversation, run=run, criteria=[_GOAL_TABLE]
        )
        assert not checked.all_passed and checked.checks[0].reason == "table_too_large"


_REPORT_PATH = "결과/목록.csv"
_CROSS_RUN_CRITERIA = [_GOAL_TABLE, {"kind": "nonempty_file", "logical_path": _REPORT_PATH}]


async def _assess_run(
    conversation_id: UUID,
    run_id: UUID,
    *,
    previous_evidence: dict | None = None,
) -> GoalEvidence:
    async with TestSession() as db:
        conversation = await db.get(Conversation, conversation_id)
        run = await db.get(ConversationRun, run_id)
        assert conversation and run
        return await inspect_goal_evidence(
            db,
            conversation=conversation,
            run=run,
            criteria=_CROSS_RUN_CRITERIA,
            previous_evidence=previous_evidence,
        )


async def test_goal_combines_versioned_evidence_from_two_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = await seed_artifact_conversation()
    cid, first_run, first_file, first_version = await _seed_run_artifact(
        tmp_path, monkeypatch, scope=scope, data=_csv(_REFERENCE)
    )
    first = await _assess_run(cid, first_run)
    assert not first.all_passed and first.progress == 1
    _, second_run, second_file, second_version = await _seed_run_artifact(
        tmp_path, monkeypatch, scope=scope, logical_path=_REPORT_PATH, data=b"report"
    )
    current_only = await _assess_run(cid, second_run)
    assert not current_only.all_passed and current_only.progress == 1
    combined = await _assess_run(cid, second_run, previous_evidence=first.model_dump(mode="json"))
    assert combined.all_passed and combined.progress == 2
    assert [(c.source_run_id, c.artifact_id, c.version_id) for c in combined.checks] == [
        (first_run, first_file, first_version),
        (second_run, second_file, second_version),
    ]


@pytest.mark.parametrize("change", ["byte_corruption", "new_version", "deleted", "foreign_run"])
async def test_goal_revalidates_prior_evidence_before_combining_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    scope = await seed_artifact_conversation()
    table = _csv(_REFERENCE)
    cid, first_run, first_file, first_version = await _seed_run_artifact(
        tmp_path, monkeypatch, scope=scope, data=table
    )
    first = await _assess_run(cid, first_run)
    assert first.checks[0].passed
    receipt = first.model_dump(mode="json")
    _, second_run, _, _ = await _seed_run_artifact(
        tmp_path, monkeypatch, scope=scope, logical_path=_REPORT_PATH, data=b"report"
    )
    async with TestSession() as db:
        artifact = await db.get(ConversationArtifact, first_file)
        version = await db.get(ArtifactVersion, first_version)
        assert artifact and version
        if change == "byte_corruption":
            path = await LocalArtifactStorageBackend().local_path(
                object_key=version.object_key, tenant_id=_CONTEXT.tenant_id
            )
            path.write_bytes(table.replace(b"150", b"151"))
        elif change == "new_version":
            version_id = uuid4()
            source = tmp_path / "replacement.csv"
            source.write_bytes(table)
            stored = await LocalArtifactStorageBackend().put_file(
                conversation_id=cid,
                tenant_id=_CONTEXT.tenant_id,
                artifact_id=first_file,
                version_number=2,
                display_name=artifact.display_name,
                source_path=source,
            )
            db.add(
                ArtifactVersion(
                    id=version_id,
                    artifact_id=first_file,
                    version_number=2,
                    object_key=stored.object_key,
                    original_filename=artifact.display_name,
                    size_bytes=len(table),
                    sha256=hashlib.sha256(table).hexdigest(),
                )
            )
            artifact.current_version_id = version_id
        elif change == "deleted":
            artifact.status = "deleted"
        else:
            _, foreign_run, _, _ = await _seed_run_artifact(tmp_path, monkeypatch, data=table)
            receipt["checks"][0]["source_run_id"] = str(foreign_run)
        await db.commit()
    combined = await _assess_run(cid, second_run, previous_evidence=receipt)
    assert not combined.all_passed and combined.progress == 1
    assert not combined.checks[0].passed and combined.checks[1].passed


@pytest.mark.parametrize("replacement", ["wrong_content", "deleted", "missing_version"])
async def test_new_run_output_shadows_old_passing_evidence_at_the_same_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    scope = await seed_artifact_conversation()
    cid, first_run, _, _ = await _seed_run_artifact(
        tmp_path, monkeypatch, scope=scope, data=_csv(_REFERENCE)
    )
    first = await _assess_run(cid, first_run)
    assert first.checks[0].passed
    _, second_run, new_file, _ = await _seed_run_artifact(
        tmp_path,
        monkeypatch,
        scope=scope,
        data=b"wrong" if replacement == "wrong_content" else _csv(_REFERENCE),
    )
    async with TestSession() as db:
        artifact = await db.get(ConversationArtifact, new_file)
        assert artifact
        if replacement == "deleted":
            artifact.status = "deleted"
        elif replacement == "missing_version":
            artifact.current_version_id = None
        await db.commit()
    combined = await _assess_run(cid, second_run, previous_evidence=first.model_dump(mode="json"))
    assert not combined.checks[0].passed
    assert combined.progress == 0


async def test_old_receipt_cannot_skip_a_rewrite_in_an_intervening_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = await seed_artifact_conversation()
    cid, first_run, _, _ = await _seed_run_artifact(
        tmp_path, monkeypatch, scope=scope, data=_csv(_REFERENCE)
    )
    first = await _assess_run(cid, first_run)
    assert first.checks[0].passed
    await _seed_run_artifact(tmp_path, monkeypatch, scope=scope, data=_csv(_REFERENCE[:1]))
    _, last_run, _, _ = await _seed_run_artifact(
        tmp_path, monkeypatch, scope=scope, logical_path=_REPORT_PATH, data=b"report"
    )
    combined = await _assess_run(cid, last_run, previous_evidence=first.model_dump(mode="json"))
    assert not combined.all_passed and combined.progress == 1
    assert not combined.checks[0].passed and combined.checks[1].passed


async def test_goal_evidence_caps_total_bytes_across_distinct_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(conversation_goal_evidence, "MAX_GOAL_EVIDENCE_BYTES", 12)
    scope = await seed_artifact_conversation()
    cid, rid, _, _ = await _seed_run_artifact(tmp_path, monkeypatch, scope=scope, data=b"12345678")
    _, _, second_file, _ = await _seed_run_artifact(
        tmp_path, monkeypatch, scope=scope, logical_path=_REPORT_PATH, data=b"87654321"
    )
    async with TestSession() as db:
        artifact = await db.get(ConversationArtifact, second_file)
        conversation, run = await db.get(Conversation, cid), await db.get(ConversationRun, rid)
        assert artifact and conversation and run
        artifact.assistant_msg_id = str(rid)
        await db.commit()
        result = await inspect_goal_evidence(
            db,
            conversation=conversation,
            run=run,
            criteria=[
                {"kind": "nonempty_file", "logical_path": path}
                for path in (_TABLE_PATH, _REPORT_PATH)
            ],
        )
        assert not result.all_passed and result.progress < 2
        assert any(check.reason == "evidence_too_large" for check in result.checks)


async def test_goal_evidence_reads_shared_version_once_for_different_conditions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = _csv(_REFERENCE)
    monkeypatch.setattr(conversation_goal_evidence, "MAX_GOAL_EVIDENCE_BYTES", len(table))
    cid, rid, _, _ = await _seed_run_artifact(tmp_path, monkeypatch, data=table)
    read = conversation_goal_evidence._read_verified
    paths: list[Path] = []

    def count_read(path: Path, *, object_key: str, size: int, sha256: str) -> bytes:
        paths.append(path)
        return read(path, object_key=object_key, size=size, sha256=sha256)

    monkeypatch.setattr(conversation_goal_evidence, "_read_verified", count_read)
    async with TestSession() as db:
        conversation, run = await db.get(Conversation, cid), await db.get(ConversationRun, rid)
        assert conversation and run
        result = await inspect_goal_evidence(
            db,
            conversation=conversation,
            run=run,
            criteria=[{"kind": "nonempty_file", "logical_path": _TABLE_PATH}, _GOAL_TABLE],
        )
    assert result.all_passed and result.progress == 2
    assert len(paths) == 1
