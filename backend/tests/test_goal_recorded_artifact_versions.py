"""Goal evidence must read the immutable version created by either chat recorder."""

import hashlib
from pathlib import Path
from typing import NamedTuple

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.conversation import Conversation
from app.models.conversation_artifact import ArtifactVersion
from app.models.conversation_run import ConversationRun
from app.services.artifacts.errors import ArtifactNotFoundError
from app.services.artifacts.recorder import ArtifactDeltaRecorder, ArtifactRuntimeContext
from app.services.conversation_goal_evidence import (
    inspect_goal_evidence,
    read_goal_evidence_version,
)
from app.services.conversation_run_fence import claim_run_owner, run_fence_scope
from tests.artifact_test_helpers import seed_artifact_conversation
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession

_ORIGINAL = b"company,price\nA,20\n"
_REVISED = b"company,price\nA,20\nB,35\n"
_TABLE = {
    "kind": "csv_table",
    "logical_path": "report.csv",
    "required_columns": ["company", "price"],
    "unique_by": "company",
    "minimum_rows": 2,
    "reference_rows": [{"company": "A", "price": "20"}, {"company": "B", "price": "35"}],
}


class RecordedVersions(NamedTuple):
    conversation: Conversation
    run: ConversationRun
    first: ArtifactVersion
    current: ArtifactVersion


@pytest.fixture(params=[False, True], ids=["direct", "fenced"])
async def records(
    db: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> RecordedVersions:
    managed = request.param
    conversation_id, agent_id = await seed_artifact_conversation()
    org = unit_context(TEST_USER_ID)
    store = tmp_path / "storage"
    monkeypatch.setattr(settings, "artifact_storage_dir", str(store))
    run = ConversationRun(
        conversation_id=conversation_id,
        agent_id=agent_id,
        user_id=org.user_id,
        tenant_id=org.tenant_id,
        org_id=org.org_id,
        source="chat",
        status="running",
        is_active=True,
    )
    db.add(run)
    await db.flush()
    fence = claim_run_owner(run, "writer") if managed else None
    run_id = run.id
    await db.commit()

    output = tmp_path / "output"
    output.mkdir()
    with run_fence_scope(fence):
        recorder = ArtifactDeltaRecorder(
            session_factory=TestSession,
            context=ArtifactRuntimeContext(conversation_id, org, agent_id, str(run_id), output),
        )
        await recorder.prepare()
        for tool, data in (("write_file", _ORIGINAL), ("edit_file", _REVISED)):
            (output / "report.csv").write_bytes(data)
            assert await recorder.collect_after_tool_result(tool_name=tool, tool_call_id=tool)

    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    run.status, run.is_active = "completed", False
    await db.commit()
    first, current = (
        await db.scalars(select(ArtifactVersion).order_by(ArtifactVersion.version_number))
    ).all()
    for version, data in ((first, _ORIGINAL), (current, _REVISED)):
        expected = Path(
            "tenants",
            str(org.tenant_id),
            "conversations",
            str(conversation_id),
            str(version.artifact_id),
            f"v{version.version_number}",
        )
        if managed:
            expected /= str(version.id)
        assert version.object_key == str(expected / "report.csv")
        assert (store / version.object_key).read_bytes() == data
        assert version.sha256 == hashlib.sha256(data).hexdigest()

    return RecordedVersions(conversation, run, first, current)


async def test_goal_reads_the_versions_committed_by_the_chat_recorder(
    db: AsyncSession,
    records: RecordedVersions,
) -> None:
    conversation, run, first, current = records
    evidence = await inspect_goal_evidence(
        db, conversation=conversation, run=run, criteria=[_TABLE]
    )
    [check] = evidence.checks
    assert evidence.all_passed, check.reason
    assert (check.source_run_id, check.artifact_id, check.version_id, check.sha256) == (
        run.id,
        first.artifact_id,
        current.id,
        hashlib.sha256(_REVISED).hexdigest(),
    )
    version, content = await read_goal_evidence_version(
        db,
        context=unit_context(TEST_USER_ID),
        conversation_id=conversation.id,
        artifact_id=first.artifact_id,
        version_id=first.id,
    )
    assert version.id == first.id and content == _ORIGINAL


@pytest.mark.parametrize("invalid_object", ["filename", "version_id", "noncanonical", "symlink"])
async def test_pinned_file_rejects_storage_identity_mismatch_even_with_the_same_bytes(
    db: AsyncSession, records: RecordedVersions, tmp_path: Path, invalid_object: str
) -> None:
    first, second = records.first, records.current
    store = tmp_path / "storage"
    key = Path(first.object_key)
    if invalid_object == "filename":
        wrong_name = key.with_name("different.csv")
        (store / wrong_name).write_bytes(_ORIGINAL)
        first.object_key = str(wrong_name)
    elif invalid_object == "version_id":
        root = key.parent.parent if key.parent.name == str(first.id) else key.parent
        other_version = root / str(second.id) / key.name
        (store / other_version).parent.mkdir()
        (store / other_version).write_bytes(_ORIGINAL)
        first.object_key = str(other_version)
    elif invalid_object == "noncanonical":
        placeholder = key.parent / "extra"
        (store / placeholder).mkdir()
        first.object_key = str(placeholder / ".." / key.name)
    else:
        # Keep the declared key unchanged but replace its file with a link
        # to an identically hashed sibling under this test's storage root.
        actual = store / key
        sibling = actual.with_name("sibling.csv")
        actual.rename(sibling)
        actual.symlink_to(sibling)
    await db.commit()
    with pytest.raises(ArtifactNotFoundError):
        await read_goal_evidence_version(
            db,
            context=unit_context(TEST_USER_ID),
            conversation_id=records.conversation.id,
            artifact_id=first.artifact_id,
            version_id=first.id,
        )
