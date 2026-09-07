from __future__ import annotations

from app.routers.conversation_agent_protocol_contracts import (
    AgentCommandParams,
    AgentCommandRequest,
    ThreadCheckpoint,
)
from app.routers.conversation_agent_protocol_runtime import checkpoint_id


def test_checkpoint_id_reads_sdk_fork_config() -> None:
    command = AgentCommandRequest(
        method="run.start",
        params=AgentCommandParams(
            input=None,
            config={"configurable": {"checkpoint_id": "ck-sdk-fork"}},
        ),
    )

    assert checkpoint_id(command) == "ck-sdk-fork"


def test_checkpoint_id_prefers_explicit_checkpoint_param() -> None:
    command = AgentCommandRequest(
        method="run.start",
        params=AgentCommandParams(
            checkpoint=ThreadCheckpoint(checkpoint_id="ck-explicit"),
            config={"configurable": {"checkpoint_id": "ck-sdk-fork"}},
        ),
    )

    assert checkpoint_id(command) == "ck-explicit"
