"""Cross-version PostgreSQL contract for a delegated approval issued before the child ledger.

Supply INTEGRATION_DATABASE_URL for a disposable moldy_pg_lane_* database and an
isolated checkout and environment for the old backend through MOLDY_LEGACY_BACKEND
and MOLDY_LEGACY_PYTHON. The archived checkout is commit 631ae500, with its locked
Deep Agents 0.7.32 and LangGraph 1.2.19. Run this optional release check explicitly
with ``pytest -m integration tests/integration/ag_ui_legacy_live.py``. Both graph
generations use deterministic local models.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.approval_request import ApprovalRequest
from app.models.conversation_child_task import ConversationChildTask
from app.models.model import Model
from tests.authz.unit_scope_fixture import unit_context
from tests.integration.ag_ui_legacy_checks import OldRun, assert_standard_resume
from tests.integration.test_conversation_queue_concurrency import (
    _postgres_session_factory,
    _seed_conversation,
)


async def _old_command(script: str, args: list[str], *, dsn: str) -> dict[str, Any]:
    path = Path(__file__).with_name(script)
    old_source, old_python = os.environ["MOLDY_LEGACY_BACKEND"], os.environ["MOLDY_LEGACY_PYTHON"]
    env = {
        **os.environ,
        "PYTHONPATH": old_source,
        "DATABASE_URL": dsn.replace("postgresql:", "postgresql+asyncpg:", 1),
        "DATABASE_URL_SYNC": dsn,
        "AG_UI_LEGACY_CHECKPOINT_DSN": dsn,
        "E2E_SCRIPTED_MODEL_ENABLED": "true",
    }
    proc = await asyncio.create_subprocess_exec(
        old_python,
        str(path),
        *args,
        cwd=old_source,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(120):
            stdout, stderr = await proc.communicate()
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    assert proc.returncode == 0, (stdout.decode()[-3000:], stderr.decode()[-3000:])
    return json.loads(stdout.decode().strip().splitlines()[-1])


@pytest.fixture
async def old_run(request: pytest.FixtureRequest) -> AsyncIterator[OldRun]:
    if not all(os.getenv(name) for name in ("MOLDY_LEGACY_BACKEND", "MOLDY_LEGACY_PYTHON")):
        pytest.skip("Set the archived pre-F04 backend path and its locked 0.7.32 interpreter")
    sessions, engine = _postgres_session_factory()
    try:
        user, root_id, conversation = await _seed_conversation(sessions)
        scope = unit_context(user)
        async with sessions() as db:
            root = await db.get(Agent, root_id)
            assert root is not None
            model = await db.get(Model, root.model_id)
            assert model is not None
            model.provider = "e2e_scripted"
            child = Agent(
                user_id=user,
                org_id=scope.org_id,
                tenant_id=scope.tenant_id,
                name="Historical reviewer",
                model_id=root.model_id,
                system_prompt="Review this synthetic draft.",
            )
            db.add(child)
            await db.flush()
            db.add(
                AgentSubAgentLink(parent_agent_id=root_id, sub_agent_id=child.id, delegated_by=user)
            )
            await db.commit()

        url = make_url(os.environ["INTEGRATION_DATABASE_URL"]).set(drivername="postgresql")
        dsn = url.render_as_string(hide_password=False)
        scope_args = [
            "--user-id",
            str(user),
            "--tenant-id",
            str(scope.tenant_id),
            "--org-id",
            str(scope.org_id),
            "--conversation-id",
            str(conversation),
        ]
        journal, approval = request.param
        issued = await _old_command(
            "ag_ui_legacy_run.py",
            [
                "issue",
                *scope_args,
                "--root-id",
                str(root_id),
                "--child-name",
                child.runtime_name,
                "--journal",
                journal,
                "--approval",
                approval,
            ],
            dsn=dsn,
        )
        run = UUID(issued["runId"])
        assert (issued["deepagents"], issued["langgraph"]) == ("0.7.32", "1.2.19")
        assert issued["childMetadata"]["lc_agent_name"] == child.runtime_name
        async with sessions() as db:
            assert not await db.scalar(
                select(ConversationChildTask.id).where(ConversationChildTask.run_id == run)
            )
            row = await db.scalar(select(ApprovalRequest).where(ApprovalRequest.run_id == run))
            assert (row is not None) == (approval == "recorded")
        yield OldRun(
            sessions, dsn, user, conversation, root_id, child.id, child.runtime_name, run, issued
        )
    finally:
        await engine.dispose()


@pytest.mark.parametrize("old_run", [("retained", "recorded"), ("absent", "absent")], indirect=True)
async def test_old_transport_completes_its_original_child_approval(old_run: OldRun) -> None:
    case = old_run
    result = await _old_command(
        "ag_ui_legacy_v3.py",
        [
            "--conversation-id",
            str(case.conversation_id),
            "--run-id",
            str(case.run_id),
            "--user-id",
            str(case.user_id),
            "--org-id",
            str(unit_context(case.user_id).org_id),
            "--child-name",
            case.child_name,
            "--interrupt-id",
            case.issued["interruptId"],
        ],
        dsn=case.dsn,
    )
    assert result["accepted"] and result["effects"] == 1 and result["pending"] == 0


@pytest.mark.parametrize(
    ("old_run", "revoked"),
    [
        (("retained", "recorded"), False),
        (("retained", "absent"), False),
        (("absent", "absent"), False),
        (("absent", "absent"), True),
    ],
    indirect=["old_run"],
    ids=["recorded", "recorder-race", "checkpoint-only", "revoked-without-journal"],
)
async def test_standard_transport_reauthorizes_the_original_child(
    old_run: OldRun, revoked: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    await assert_standard_resume(old_run, revoked, monkeypatch)
