"""Conversation-owned rows and bytes for the B08 lifecycle fixture."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Final
from uuid import uuid4

from anyio import Path as AsyncPath
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.run_metrics_types import RunMetricActivity
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.models.conversation_pinned_summary import ConversationPinnedSummary
from app.models.conversation_run_metrics import ConversationRunMetrics
from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.message_attachment import MessageAttachment
from app.models.message_event import MessageEvent, MessageEventChunk
from app.models.message_feedback import MessageFeedback
from app.models.share_link import ShareLink
from app.models.token_usage import TokenUsage
from tests.authz.agent_lifecycle_fixture import LifecycleActors, LifecycleGraph

ATTACHMENT_BYTES: Final = b"b08-attachment-bytes\x00\x01"
ARTIFACT_VERSION_BYTES: Final = (
    b"b08-artifact-version-one\n",
    b"b08-artifact-version-two-with-delta\n",
)


async def seed_history_rows(
    db: AsyncSession,
    actors: LifecycleActors,
    graph: LifecycleGraph,
) -> None:
    """Persist conversation descendants and their exact owned byte files."""
    attachment_path = AsyncPath(graph.attachment_path)
    version_paths = tuple(AsyncPath(path) for path in graph.artifact_version_paths)
    await attachment_path.parent.mkdir(parents=True, exist_ok=True)
    await attachment_path.write_bytes(ATTACHMENT_BYTES)
    for path, payload in zip(version_paths, ARTIFACT_VERSION_BYTES, strict=True):
        await path.write_bytes(payload)
    suffix = graph.root_agent_id.hex[:12]
    assistant_id = f"b08-assistant-{suffix}"
    message_event_id = uuid4()
    event_id = f"{assistant_id}-1"
    event_payload = [{"id": event_id, "event": "message_end", "data": {"ok": True}}]
    db.add(
        MessageEvent(
            id=message_event_id,
            conversation_id=graph.conversation_ids[0],
            assistant_msg_id=assistant_id,
            events=event_payload,
            last_event_id=event_id,
            linked_message_ids=[assistant_id],
            status="completed",
        )
    )
    await db.flush()
    db.add(
        MessageEventChunk(
            id=uuid4(),
            message_event_id=message_event_id,
            conversation_id=graph.conversation_ids[0],
            assistant_msg_id=assistant_id,
            seq_start=1,
            seq_end=1,
            first_event_id=event_id,
            last_event_id=event_id,
            event_ids=[event_id],
            events=event_payload,
        )
    )
    db.add(
        MessageAttachment(
            id=graph.attachment_id,
            tenant_id=actors.tenant_id,
            org_id=actors.org_id,
            user_id=actors.owner_one_id,
            conversation_id=graph.conversation_ids[0],
            message_id=f"b08-user-{suffix}",
            filename="attachment.bin",
            mime_type="application/octet-stream",
            size_bytes=len(ATTACHMENT_BYTES),
            storage_path=str(graph.attachment_path),
            url="/fixture/attachment.bin",
        )
    )
    db.add(
        MessageFeedback(
            id=uuid4(),
            user_id=actors.owner_one_id,
            message_id=assistant_id,
            conversation_id=graph.conversation_ids[0],
            rating="up",
            comment="fixture feedback",
        )
    )
    summary = b"Pinned lifecycle result"
    db.add(
        ConversationPinnedSummary(
            conversation_id=graph.conversation_ids[0],
            source_message_id=assistant_id,
            source_branch_checkpoint_id=graph.checkpoint_ids[-1],
            snapshot_text=summary.decode(),
            source_text_hash=hashlib.sha256(summary).hexdigest(),
        )
    )
    version_hashes = tuple(
        hashlib.sha256(payload).hexdigest() for payload in ARTIFACT_VERSION_BYTES
    )
    artifact = ConversationArtifact(
        id=graph.artifact_id,
        tenant_id=actors.tenant_id,
        org_id=actors.org_id,
        user_id=actors.owner_one_id,
        agent_id=graph.root_agent_id,
        conversation_id=graph.conversation_ids[0],
        assistant_msg_id=assistant_id,
        tool_call_id=f"b08-tool-{suffix}",
        source_tool_name="fixture_tool",
        logical_path="artifacts/result.bin",
        display_name="result.bin",
        extension="bin",
        mime_type="application/octet-stream",
        artifact_kind="data",
        size_bytes=len(ARTIFACT_VERSION_BYTES[-1]),
        sha256=version_hashes[-1],
        current_version_id=None,
        status="ready",
        is_favorite=True,
        branch_checkpoint_id=graph.checkpoint_ids[-1],
        linked_message_ids=[assistant_id],
        metadata_json={"fixture": True},
    )
    db.add(artifact)
    await db.flush()
    for index, version_id in enumerate(graph.artifact_version_ids):
        payload = ARTIFACT_VERSION_BYTES[index]
        db.add(
            ArtifactVersion(
                id=version_id,
                artifact_id=graph.artifact_id,
                version_number=index + 1,
                storage_provider="local",
                object_key=str(graph.artifact_version_paths[index]),
                original_filename=f"result-v{index + 1}.bin",
                size_bytes=len(payload),
                sha256=version_hashes[index],
                metadata_json={"fixture": True},
            )
        )
    await db.flush()
    artifact.current_version_id = graph.artifact_version_ids[-1]
    for index, run_id in enumerate(graph.run_ids):
        db.add(
            ConversationRunMetrics(
                run_id=run_id,
                terminal_state=None,
                elapsed_ms=10.0 + index,
                ttft_ms=2.0,
                prompt_tokens=10 + index,
                completion_tokens=5 + index,
                usage_complete=True,
                root_tool_calls=1,
                descendant_tool_calls=2,
                root_subagent_calls=2,
                descendant_subagent_calls=0,
                activity_json=[
                    RunMetricActivity(
                        kind="tool_call",
                        namespace=(),
                        call_id=f"fixture-call-{index}",
                        name="fixture_tool",
                        elapsed_ms=1.0 + index,
                    ).to_persistence_payload()
                ],
            )
        )
    db.add(
        TokenUsage(
            id=uuid4(),
            run_id=graph.run_ids[0],
            conversation_id=graph.conversation_ids[0],
            agent_id=graph.root_agent_id,
            model_name="fixture-model",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            estimated_cost=Decimal("0.000015"),
        )
    )
    db.add(
        ShareLink(
            id=graph.share_link_id,
            tenant_id=actors.tenant_id,
            org_id=actors.org_id,
            share_token=f"b08-share-{suffix}",
            conversation_id=graph.conversation_ids[0],
            created_by=actors.owner_one_id,
        )
    )
    mcp_server_id = uuid4()
    mcp_tool_id = uuid4()
    db.add(
        McpServer(
            id=mcp_server_id,
            tenant_id=actors.tenant_id,
            org_id=actors.org_id,
            user_id=actors.owner_one_id,
            name=f"B08 static MCP {suffix}",
            description="fixture",
            transport="streamable_http",
            url="http://127.0.0.1.invalid/mcp",
            credential_id=None,
            status="disabled",
            is_system=False,
        )
    )
    await db.flush()
    db.add(
        McpTool(
            id=mcp_tool_id,
            server_id=mcp_server_id,
            name="fixture_static_tool",
            description="fixture",
            input_schema={"type": "object"},
            metadata_json={"ui": {"resourceUri": "ui://fixture/static"}},
            enabled=True,
        )
    )
    await db.flush()
    db.add(
        McpAppInvocationBinding(
            id=graph.mcp_binding_id,
            run_id=graph.run_ids[0],
            conversation_id=graph.conversation_ids[0],
            user_id=actors.owner_one_id,
            agent_id=graph.root_agent_id,
            credential_subject_user_id=actors.owner_one_id,
            mcp_server_id=mcp_server_id,
            mcp_tool_id=mcp_tool_id,
            tool_call_id=f"b08-tool-{suffix}",
            remote_tool_name="fixture_static_tool",
            resource_uri="ui://fixture/static",
            tool_meta={"ui": {"resourceUri": "ui://fixture/static"}},
            result_meta={"bounded": True},
            structured_content={"result": "fixture"},
        )
    )
    await db.commit()


__all__ = ["ARTIFACT_VERSION_BYTES", "ATTACHMENT_BYTES", "seed_history_rows"]
