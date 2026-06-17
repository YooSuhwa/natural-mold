from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agent_runtime.skill_executor import _create_skill_execute_tool
from app.marketplace.skill_runtime import SkillRuntimeDescriptor, SkillToolContext
from app.models.audit_event import AuditEvent
from tests.conftest import TestSession

pytestmark = pytest.mark.asyncio


async def test_execute_in_skill_audits_unsupported_executable_before_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _ctx(tmp_path, slug="unsupported")
    tool = _create_skill_execute_tool(ctx)
    assert tool.coroutine is not None
    monkeypatch.setattr("app.agent_runtime.skill_executor_audit.async_session", TestSession)

    result = await tool.coroutine(
        skill_directory="/runtime/thread-sandbox/skills/unsupported/",
        command="bash scripts/run.sh --token=raw-secret",
    )
    event = await _sandbox_event("unsupported_executable")

    assert result == "Error: only python, node, or curl commands are allowed."
    assert event.reason_code == "unsupported_executable"
    assert event.event_metadata is not None
    assert event.event_metadata["command_executable"] == "bash"
    assert "raw-secret" not in str(event.event_metadata)
    assert not ctx.output_dir.exists()


async def test_execute_in_skill_audits_script_path_traversal_before_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _ctx(tmp_path, slug="traversal")
    tool = _create_skill_execute_tool(ctx)
    assert tool.coroutine is not None
    monkeypatch.setattr("app.agent_runtime.skill_executor_audit.async_session", TestSession)

    result = await tool.coroutine(
        skill_directory="/runtime/thread-sandbox/skills/traversal/",
        command="python ../escape.py",
    )
    event = await _sandbox_event("path_traversal")

    assert result == "Error: script must be within the skill directory."
    assert event.reason_code == "path_traversal"
    assert event.event_metadata is not None
    assert event.event_metadata["command_executable"] == "python"
    assert "escape.py" not in str(event.event_metadata)
    assert not ctx.output_dir.exists()


async def test_execute_in_skill_audits_timeout_policy_violation_before_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "should-not-run"
    ctx = _ctx(
        tmp_path,
        slug="timeout",
        execution_profile={"timeout_seconds": 999},
        script=f"import pathlib\npathlib.Path({str(marker)!r}).write_text('ran')\n",
    )
    tool = _create_skill_execute_tool(ctx)
    assert tool.coroutine is not None
    monkeypatch.setattr("app.agent_runtime.skill_executor_audit.async_session", TestSession)

    result = await tool.coroutine(
        skill_directory="/runtime/thread-sandbox/skills/timeout/",
        command="python scripts/run.py",
    )
    event = await _sandbox_event("timeout_policy")

    assert result == "Error: timeout_seconds must be between 0 and 420."
    assert event.reason_code == "timeout_policy"
    assert event.event_metadata is not None
    assert event.event_metadata["command_executable"] == Path(sys.executable).name
    assert "scripts/run.py" not in str(event.event_metadata)
    assert not marker.exists()
    assert not ctx.output_dir.exists()


def _ctx(
    tmp_path: Path,
    *,
    slug: str,
    execution_profile: dict[str, int] | None = None,
    script: str = "print('ok')\n",
) -> SkillToolContext:
    runtime_root = tmp_path / "runtime"
    skill_dir = runtime_root / slug
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "run.py").write_text(script)
    descriptor = SkillRuntimeDescriptor(
        id=uuid.uuid4(),
        slug=slug,
        name=slug.title(),
        description="sandbox probe",
        original_storage_path=skill_dir,
        runtime_storage_path=skill_dir,
        execution_profile=execution_profile,
    )
    return SkillToolContext(
        thread_id="thread-sandbox",
        output_dir=tmp_path / "outputs",
        runtime_root=runtime_root,
        descriptors={slug: descriptor},
        run_id="run-sandbox",
    )


async def _sandbox_event(reason_code: str) -> AuditEvent:
    async with TestSession() as db:
        return (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.action == "skill_security.sandbox_denied",
                    AuditEvent.reason_code == reason_code,
                )
            )
        ).scalar_one()
