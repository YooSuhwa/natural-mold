from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agent_runtime.skill_executor import _create_skill_execute_tool
from app.credentials import service as credential_service
from app.marketplace.skill_runtime import (
    ResolvedCredential,
    SkillRuntimeDescriptor,
    SkillToolContext,
)
from app.models.credential_audit_log import CredentialAuditLog
from app.models.user import User
from tests.conftest import TEST_USER_ID, TestSession

pytestmark = pytest.mark.asyncio


async def test_execute_in_skill_records_credential_audit_without_secret_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.agent_runtime.skill_executor_audit.async_session", TestSession)
    async with TestSession() as db:
        db.add(User(id=TEST_USER_ID, email="skill-audit@test", name="Skill Audit"))
        credential = await credential_service.create(
            db,
            user_id=TEST_USER_ID,
            definition_key="openai",
            name="skill key",
            data={"api_key": "sk-runtime-secret"},
        )
        await db.commit()
        credential_id = credential.id

    runtime_root = tmp_path / "runtime"
    skill_dir = runtime_root / "audited"
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "echo.py").write_text("import os\nprint(os.environ.get('OPENAI_API_KEY'))\n")
    agent_id = uuid.uuid4()
    run_id = str(uuid.uuid4())
    descriptor = SkillRuntimeDescriptor(
        id=uuid.uuid4(),
        slug="audited",
        name="Audited",
        description="uses a credential",
        original_storage_path=skill_dir,
        runtime_storage_path=skill_dir,
        credential_bindings={
            "openai": ResolvedCredential(
                credential_id=credential_id,
                definition_key="openai",
                env_map={"api_key": "OPENAI_API_KEY"},
                decrypted={"api_key": "sk-runtime-secret"},
            )
        },
    )
    ctx = SkillToolContext(
        thread_id="thread-audit",
        output_dir=tmp_path / "outputs",
        runtime_root=runtime_root,
        descriptors={"audited": descriptor},
        user_id=TEST_USER_ID,
        agent_id=agent_id,
        run_id=run_id,
    )
    tool = _create_skill_execute_tool(ctx)
    assert tool.coroutine is not None

    result = await tool.coroutine(
        skill_directory="/runtime/thread-audit/skills/audited/",
        command="python scripts/echo.py",
    )

    async with TestSession() as db:
        rows = (
            (
                await db.execute(
                    select(CredentialAuditLog).where(CredentialAuditLog.action == "invoke")
                )
            )
            .scalars()
            .all()
        )

    assert "sk-runtime-secret" not in result
    assert len(rows) == 1
    row = rows[0]
    assert row.credential_id == credential_id
    assert row.actor_user_id == TEST_USER_ID
    assert row.source == "runtime"
    assert row.log_metadata is not None
    assert row.log_metadata == {
        "kind": "execute_in_skill",
        "skill_id": str(descriptor.id),
        "skill_slug": "audited",
        "requirement_key": "openai",
        "thread_id": "thread-audit",
        "command_executable": Path(sys.executable).name,
        "timeout_seconds": 30.0,
        "agent_id": str(agent_id),
        "run_id": run_id,
    }
    metadata_text = str(row.log_metadata)
    assert "sk-runtime-secret" not in metadata_text
    assert "scripts/echo.py" not in metadata_text
