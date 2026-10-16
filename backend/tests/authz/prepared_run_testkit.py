"""Shared builders for prepared-run lease and registry tests."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.agent_runtime.prepared_run_digest import (
    PreparedRunDigestMaterial,
    compute_prepared_run_digest,
)
from app.agent_runtime.runtime_config import PreparedRunBinding, PreparedRunLease

TENANT_ID = uuid.UUID("10000000-0000-4000-8000-000000000001")
ORG_ID = uuid.UUID("20000000-0000-4000-8000-000000000002")
USER_ID = uuid.UUID("30000000-0000-4000-8000-000000000003")
AGENT_ID = uuid.UUID("40000000-0000-4000-8000-000000000004")
CONVERSATION_ID = uuid.UUID("50000000-0000-4000-8000-000000000005")
SECRET = "sk-prepared-run-secret-value"


def binding(run_id: uuid.UUID | None = None) -> PreparedRunBinding:
    return PreparedRunBinding(
        tenant_id=TENANT_ID,
        org_id=ORG_ID,
        user_id=USER_ID,
        agent_id=AGENT_ID,
        conversation_id=CONVERSATION_ID,
        run_id=run_id or uuid.UUID("60000000-0000-4000-8000-000000000006"),
        thread_id=str(CONVERSATION_ID),
        participant_paths=("root", "root/child"),
    )


def material(run_binding: PreparedRunBinding, **overrides: Any) -> PreparedRunDigestMaterial:
    values: dict[str, Any] = {
        "binding": run_binding,
        "versions": {"agent_lifecycle": "3", "runtime_policy": "sha-policy"},
        "snapshot_digests": {"skill:writer": "sha-skill", "memory": "sha-memory"},
        "tool_access": {"search": "resolved_external", "write_file": "run_output"},
        "checkpoint_lineage": (run_binding.thread_id, "", "checkpoint-1"),
        "secret_reference_ids": ("credential:llm", "credential:search"),
    }
    values.update(overrides)
    return PreparedRunDigestMaterial(**values)


@dataclass
class CloseProbe:
    """Counts lease cleanup calls and can block or fail on demand."""

    calls: int = 0
    delay: float = 0.0
    error: BaseException | None = None
    started: asyncio.Event = field(default_factory=asyncio.Event)

    async def __call__(self) -> None:
        self.calls += 1
        self.started.set()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error


def lease(
    run_binding: PreparedRunBinding, token: str, probe: CloseProbe | None = None
) -> PreparedRunLease:
    return PreparedRunLease(
        binding=run_binding,
        token=token,
        digest=compute_prepared_run_digest(material(run_binding)),
        runnable=object(),
        child_runnables={"child": object()},
        messages=("hello",),
        config={"configurable": {"thread_id": run_binding.thread_id}},
        checkpointer=object(),
        checkpoint_seed={"checkpoint_id": "checkpoint-1"},
        snapshots={"skill:writer": b"content"},
        tool_access={"search": "resolved_external", "write_file": "run_output"},
        secret_values=frozenset({SECRET}),
        close=probe,
    )
