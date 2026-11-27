"""Root interrupt echoes require a current, unambiguous managed child owner."""

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_events import ProtocolInterrupt
from app.services import ag_ui_snapshot
from app.services.ag_ui_snapshot_journal import SnapshotRunReplay, _ObservedOwners
from tests.authz.test_child_task_authorization import child_case as child_case
from tests.authz.test_child_task_read_bindings import bound as bound
from tests.test_ag_ui_snapshot_managed_children import (
    NATIVE,
    PUBLIC,
    SLOT,
    ChildSnapshot,
)
from tests.test_ag_ui_snapshot_managed_children import (
    pending_child as pending_child,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("witness", ["echo", "other-child", "conflict", "no-longer-waiting"])
async def test_managed_echo_can_only_restore_the_proven_slot(
    db: AsyncSession,
    pending_child: ChildSnapshot,
    monkeypatch: pytest.MonkeyPatch,
    witness: str,
) -> None:
    case = pending_child
    root = ProtocolInterrupt(id="root-slot", ns=[], value={"type": "ask_user", "question": "Root?"})
    child = ProtocolInterrupt(
        id=SLOT, ns=list(NATIVE), value={"type": "ask_user", "question": "Child?"}
    )
    native = replace(case.pending, interrupts=(root, child))
    monkeypatch.setattr(ag_ui_snapshot, "read_proven_interrupts", AsyncMock(return_value=native))
    other = ("tools:other-sibling",)
    origin: set[tuple[str, ...]] = {other} if witness == "other-child" else {NATIVE}
    if witness == "conflict":
        origin.add(other)
    if witness == "no-longer-waiting":
        case.task.status = "completed"
        await db.commit()
    replay = SnapshotRunReplay(visibility=case.visibility)
    observations = _ObservedOwners(namespaces=origin, pending={SLOT: {(), *origin}})
    observations.bind(replay, str(case.conversation.id), {})

    current, _ = await ag_ui_snapshot._current_interrupts(
        db,
        case.conversation,
        case.context,
        case.history,
        case.run,
        ag_ui_snapshot._RunEvidence.from_replay(replay),
        case.merged,
        PUBLIC,
        (),
    )
    if witness == "echo":
        assert len(case.merged.subagents) == 1
        assert case.merged.subagents[0]["childTaskId"] == str(case.task.id)
        assert [(item.id, item.subagent_run_id) for item in current] == [
            ("root-slot", None),
            (SLOT, case.merged.subagents[0]["subagentRunId"]),
        ]
        assert all(item.response_schema for item in current)
    else:
        assert [item.id for item in current] == ["root-slot"]
        assert current[0].response_schema is None
        assert (current[0].metadata or {})["moldy"]["actionable"] is False
