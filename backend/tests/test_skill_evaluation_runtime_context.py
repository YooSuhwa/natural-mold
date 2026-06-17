from __future__ import annotations

import io
import uuid
import zipfile
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder.eval_runner import run_eval_skill_command
from app.agent_runtime.skill_executor import _create_skill_execute_tool
from app.config import settings
from app.credentials import service as credential_service
from app.models.credential_audit_log import CredentialAuditLog
from app.models.marketplace import SkillCredentialBinding
from app.services import skill_evaluation_service
from app.services.skill_evaluation_worker_state import build_context
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID, TestSession
from tests.tool_helpers import tool_coroutine

pytestmark = pytest.mark.asyncio


def _skill_content(name: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        'description: "Use when testing evaluation runtime mounts."\n'
        "---\n\n"
        f"Runtime marker: {name}\n"
    )


def _zip_with(files: Mapping[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


async def test_evaluation_context_mounts_only_the_target_skill(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    with patch.object(settings, "data_root", str(tmp_path)):
        target = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Runtime Target",
            slug=f"runtime-target-{uuid.uuid4().hex[:8]}",
            description="Use when testing evaluation runtime mounts.",
            content=_skill_content("target"),
            version="1.0.0",
        )
        other = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Runtime Other",
            slug=f"runtime-other-{uuid.uuid4().hex[:8]}",
            description="Use when testing evaluation runtime mounts.",
            content=_skill_content("other"),
            version="1.0.0",
        )
        evaluation_set = await skill_evaluation_service.create_evaluation_set(
            db,
            user_id=TEST_USER_ID,
            skill=target,
            name="Runtime smoke",
            evals=[{"input": "read the skill"}],
        )
        run = await skill_evaluation_service.create_run(
            db,
            user_id=TEST_USER_ID,
            skill=target,
            evaluation_set=evaluation_set,
        )

        context = await build_context(db, run)

    runtime_context = context.runtime_context
    assert runtime_context.runtime_root == tmp_path / "runtime" / str(run.id) / "skills"
    assert runtime_context.output_dir == tmp_path / "skill-evaluation-runs" / str(run.id)
    assert list(runtime_context.descriptors) == [target.slug]
    assert other.slug not in runtime_context.descriptors

    descriptor = runtime_context.descriptors[target.slug]
    assert descriptor.runtime_storage_path == runtime_context.runtime_root / target.slug
    assert (
        (descriptor.runtime_storage_path / "SKILL.md")
        .read_text()
        .endswith("Runtime marker: target\n")
    )
    scripts_dir = descriptor.runtime_storage_path / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "read_skill.py").write_text(
        'from pathlib import Path\nprint(Path("SKILL.md").read_text())\n',
        encoding="utf-8",
    )

    execute = tool_coroutine(_create_skill_execute_tool(runtime_context))
    selected = await execute(
        skill_directory=f"/runtime/{run.id}/skills/{target.slug}/",
        command="python scripts/read_skill.py",
    )
    unselected = await execute(
        skill_directory=f"/runtime/{run.id}/skills/{other.slug}/",
        command="python scripts/read_skill.py",
    )

    assert "Runtime marker: target" in selected
    assert "not attached" in unselected
    assert "should-not-run" not in unselected


async def test_eval_command_reuses_skill_execution_policy_and_audit_kind(
    db: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.agent_runtime.skill_executor_audit.async_session", TestSession)
    secret = "sk-evaluation-secret"
    with patch.object(settings, "data_root", str(tmp_path)):
        credential = await credential_service.create(
            db,
            user_id=TEST_USER_ID,
            definition_key="openai",
            name="evaluation key",
            data={"api_key": secret},
        )
        skill = await skill_service.create_package_skill(
            db,
            user_id=TEST_USER_ID,
            zip_bytes=_zip_with(
                {
                    "SKILL.md": _skill_content("eval-package"),
                    "scripts/echo_env.py": (
                        "import os\n"
                        "from pathlib import Path\n"
                        "print(os.environ['OPENAI_API_KEY'])\n"
                        "print(Path(os.environ['HOME']).name)\n"
                        "print(Path(os.environ['OUTPUTS_DIR']).resolve())\n"
                        "Path(os.environ['SKILL_OUTPUT_DIR'], 'result.txt').write_text('ok')\n"
                    ),
                }
            ),
        )
        skill.credential_requirements = [
            {
                "key": "openai",
                "definition_key": "openai",
                "required": True,
                "label": "OpenAI",
                "fields": ["api_key"],
                "injection": "env",
                "scope": "user",
                "env_map": {"api_key": "OPENAI_API_KEY"},
            }
        ]
        db.add(
            SkillCredentialBinding(
                skill_id=skill.id,
                user_id=TEST_USER_ID,
                requirement_key="openai",
                credential_id=credential.id,
                scope="skill",
            )
        )
        evaluation_set = await skill_evaluation_service.create_evaluation_set(
            db,
            user_id=TEST_USER_ID,
            skill=skill,
            name="Runtime policy",
            evals=[{"input": "read the skill"}],
        )
        run = await skill_evaluation_service.create_run(
            db,
            user_id=TEST_USER_ID,
            skill=skill,
            evaluation_set=evaluation_set,
        )
        context = await build_context(db, run)

    output = await run_eval_skill_command(
        context.runtime_context,
        skill_slug=skill.slug,
        command="python scripts/echo_env.py",
    )
    result = await db.execute(
        select(CredentialAuditLog).where(CredentialAuditLog.action == "invoke")
    )
    audit = result.scalar_one()

    assert secret not in output
    assert "<redacted:OPENAI_API_KEY>" in output
    assert "OUTPUT_FILES: result.txt" in output
    assert str(tmp_path / "skill-evaluation-runs" / str(run.id)) in output
    assert audit.log_metadata is not None
    assert audit.log_metadata["kind"] == "skill_evaluation"
    assert audit.log_metadata["run_id"] == str(run.id)
    assert audit.log_metadata["thread_id"] == str(run.id)
    assert "echo_env.py" not in str(audit.log_metadata)
    assert secret not in str(audit.log_metadata)
