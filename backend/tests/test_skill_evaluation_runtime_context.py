from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_executor import _create_skill_execute_tool
from app.config import settings
from app.services import skill_evaluation_service
from app.services.skill_evaluation_worker_state import build_context
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID
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

    execute = tool_coroutine(_create_skill_execute_tool(runtime_context))
    selected = await execute(
        skill_directory=f"/runtime/{run.id}/skills/{target.slug}/",
        command="python -c 'from pathlib import Path; print(Path(\"SKILL.md\").read_text())'",
    )
    unselected = await execute(
        skill_directory=f"/runtime/{run.id}/skills/{other.slug}/",
        command="python -c 'print(\"should-not-run\")'",
    )

    assert "Runtime marker: target" in selected
    assert "not attached" in unselected
    assert "should-not-run" not in unselected
