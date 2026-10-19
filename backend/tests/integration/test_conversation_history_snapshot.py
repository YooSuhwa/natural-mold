from __future__ import annotations

import json
import os
from uuid import UUID, uuid4

import pytest
from anyio import Path
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.conversation_history import (
    ConversationHistoryCheckpoint,
    ConversationHistoryEvent,
)
from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.user import User
from app.services.conversation_history_secrets import collect_strict_history_secrets
from app.services.conversation_history_snapshot import (
    ConversationHistoryMaterializationRequest,
    materialize_conversation_history,
)
from tests.authz.agent_lifecycle_fixture import registered_lifecycle_fixture
from tests.integration.conversation_history_snapshot_native_fixture import (
    native_history_secrets,
    prepare_native_history_sources,
)

pytestmark = pytest.mark.integration


async def test_retained_history_materializes_after_owner_membership_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets = native_history_secrets()
    async with registered_lifecycle_fixture(monkeypatch) as fixture:
        graph = fixture.graph
        async with fixture.sessions() as db:
            decoy_server = McpServer(
                id=UUID(int=1),
                tenant_id=fixture.actors.tenant_id,
                org_id=fixture.actors.org_id,
                user_id=fixture.actors.owner_two_id,
                name="Task7 ordered decoy",
                transport="streamable_http",
                url="http://127.0.0.1.invalid/decoy",
                status="disabled",
                is_system=False,
            )
            db.add(decoy_server)
            await db.flush()
            db.add(
                McpTool(
                    id=UUID(int=1),
                    server_id=decoy_server.id,
                    name="task7_ordered_decoy",
                    input_schema={"type": "object"},
                    enabled=True,
                )
            )
            await db.commit()
            binding = await db.get(McpAppInvocationBinding, graph.mcp_binding_id)
            first_tool = await db.scalar(select(McpTool).order_by(McpTool.id).limit(1))
            assert binding is not None and first_tool is not None
            assert first_tool.id != binding.mcp_tool_id
        preparation = await prepare_native_history_sources(fixture, secrets)
        url = (
            make_url(os.environ["INTEGRATION_DATABASE_URL"])
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )
        async with await AsyncConnection[DictRow].connect(
            url,
            autocommit=True,
            row_factory=dict_row,
        ) as connection:
            saver = AsyncPostgresSaver(connection)
            branch_config: RunnableConfig = {
                "configurable": {
                    "thread_id": str(graph.conversation_ids[0]),
                    "checkpoint_id": graph.checkpoint_ids[-1],
                    "checkpoint_ns": "",
                }
            }
            branch = await saver.aget_tuple(branch_config)
            assert branch is not None
            retained = empty_checkpoint()
            retained["id"] = f"task7-secret-{uuid4().hex}"
            retained["channel_values"] = {
                "messages": [
                    HumanMessage(id="b08-user-message", content="fixture question"),
                    AIMessage(
                        id="b08-assistant-message",
                        content=f"fixture {' '.join(secrets.values)}",
                    ),
                ],
                "branch_header": {"name": "task7-native", "parent": graph.checkpoint_ids[-1]},
            }
            retained["channel_versions"] = {"messages": "3", "branch_header": "3"}
            await saver.aput(
                branch.config,
                retained,
                {"source": "loop", "step": 2},
                {"messages": "3", "branch_header": "3"},
            )

            async with fixture.sessions() as db:
                actor_row = await db.get(User, fixture.actors.direct_org_admin_id)
                assert actor_row is not None
                collected = await collect_strict_history_secrets(
                    db, graph.root_agent_id, graph.conversation_ids[0]
                )
                missing_source_labels = tuple(
                    label for label, value in secrets.labeled_values if value not in collected
                )
                assert not missing_source_labels, (
                    f"missing runtime secret categories: {missing_source_labels}"
                )
                actor = CurrentUser(
                    id=actor_row.id,
                    email=actor_row.email,
                    name=actor_row.name,
                    display_name=actor_row.display_name,
                )
                manifest = await materialize_conversation_history(
                    ConversationHistoryMaterializationRequest(
                        db,
                        actor,
                        graph.root_agent_id,
                        graph.conversation_ids[0],
                        "Lifecycle root",
                        saver,
                    )
                )
                await db.commit()
                checkpoints = list(
                    await db.scalars(
                        select(ConversationHistoryCheckpoint).where(
                            ConversationHistoryCheckpoint.manifest_id == manifest.id
                        )
                    )
                )
                events = list(
                    await db.scalars(
                        select(ConversationHistoryEvent).where(
                            ConversationHistoryEvent.manifest_id == manifest.id
                        )
                    )
                )
                source_agent = await db.get(Agent, graph.root_agent_id)
                serialized = json.dumps(
                    {
                        "checkpoints": [
                            {
                                "values": row.channel_values,
                                "messages": row.messages,
                                "trace": row.trace,
                            }
                            for row in checkpoints
                        ],
                        "events": [row.events for row in events],
                    },
                    sort_keys=True,
                )
                assert source_agent is not None
                assert manifest.state == "complete"
                assert manifest.checkpoint_count == 3
                leaked_labels = tuple(
                    label for label, value in secrets.labeled_values if value in serialized
                )
                assert not leaked_labels, f"retained secret categories: {leaked_labels}"
                assert graph.checkpoint_ids[-1] in {row.parent_checkpoint_id for row in checkpoints}
                assert "ui://fixture/static" in serialized

                receipt = {
                    "manifest_id": str(manifest.id),
                    "checkpoint_count": manifest.checkpoint_count,
                    "event_count": manifest.event_count,
                    "checksum": manifest.canonical_payload_sha256,
                    "secret_hits": sum(serialized.count(secret) for secret in secrets.values),
                    "source_agent_present": source_agent.id == graph.root_agent_id,
                    "owner_membership_status": preparation.owner_membership_status,
                    "provider_invocations": fixture.sentinel.provider_invocations,
                }
                receipt_path = os.environ.get("TASK7_EVIDENCE_PATH")
                output_path = (
                    Path(receipt_path)
                    if receipt_path is not None
                    else Path(os.environ["MOLDY_TEST_RUN_ROOT"])
                    / "task7-conversation-history-receipt.json"
                )
                await output_path.write_text(
                    json.dumps(receipt, sort_keys=True),
                    encoding="utf-8",
                )
