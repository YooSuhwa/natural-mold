"""Resolve a public run using its owning conversation and current read permissions."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass

import orjson
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.ag_ui_v1 import AG_UI_PROJECTION_VERSION, ChildVisibility
from app.agent_runtime.ag_ui_v1_issuance import ToolContinuation
from app.authz.org_context import OrgContext
from app.error_codes import resume_not_found
from app.exceptions import ConflictError
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_run import ConversationRun
from app.services.ag_ui_attachment_access import readable_artifact_versions
from app.services.ag_ui_commands import load_public_run_id, load_run_binding
from app.services.ag_ui_read_access import load_child_visibility, require_read_run
from app.services.ag_ui_read_cursor import CursorScope
from app.services.ag_ui_read_pending import constrain_legacy_child_visibility
from app.services.ag_ui_spawn_parent import spawned_parent_public_id
from app.services.ag_ui_tool_continuations import ContinuationCache, read_tool_continuations


def snapshot_required() -> ConflictError:
    return ConflictError("AG_UI_SNAPSHOT_REQUIRED", "현재 대화 스냅샷을 다시 읽어 주세요.")


@dataclass(frozen=True, slots=True)
class ReadTarget:
    public_id: str
    run: ConversationRun | None
    input_id: uuid.UUID | None
    input_status: str | None
    position: int | None
    revision: int | None
    version: str
    parent_public_id: str | None
    scope: CursorScope
    visibility: ChildVisibility
    artifacts: frozenset[tuple[str, str]]
    delegated: bool = False
    tool_continuations: tuple[ToolContinuation, ...] = ()


def cursor_scope_for(
    conversation: Conversation,
    public_id: str,
    run_id: uuid.UUID | None,
    version: str,
    start_checkpoint: str | None,
    visibility: ChildVisibility,
    artifacts: frozenset[tuple[str, str]],
    parent_id: str | None,
    tool_continuations: tuple[ToolContinuation, ...],
) -> CursorScope:
    branch = orjson.dumps([start_checkpoint, conversation.active_branch_checkpoint_id])
    access = orjson.dumps(
        [
            visibility.allow_legacy_details,
            sorted(visibility.tool_call_ids),
            sorted(visibility.namespaces),
            sorted(visibility.bindings),
            sorted(artifacts),
            parent_id,
            [asdict(call) for call in sorted(tool_continuations, key=lambda item: item.public_id)],
        ]
    )
    return CursorScope(
        thread_id=str(conversation.id),
        public_run_id=public_id,
        physical_run_id=str(run_id) if run_id else None,
        projection_version=version,
        branch_key=hashlib.sha256(branch).hexdigest(),
        visibility_key=hashlib.sha256(access).hexdigest(),
    )


async def resolve_read_target(
    db: AsyncSession,
    conversation: Conversation,
    context: OrgContext,
    public_id: str,
    *,
    continuation_cache: ContinuationCache | None = None,
) -> ReadTarget:
    binding = await load_run_binding(db, context, conversation.id, public_id)
    if binding is not None:
        physical_id = binding.run_id
        version = binding.projection_version
        checkpoint_id = binding.checkpoint_id
    else:
        # Only historical runs without a protocol receipt may use their physical UUID.
        try:
            physical_id = uuid.UUID(public_id)
        except ValueError as exc:
            raise resume_not_found() from exc
        if str(physical_id) != public_id:
            raise resume_not_found()
        canonical = await load_public_run_id(db, context, conversation.id, physical_id)
        if canonical is not None and canonical != public_id:
            raise resume_not_found()
        version, checkpoint_id = AG_UI_PROJECTION_VERSION, None

    if version != AG_UI_PROJECTION_VERSION:
        raise snapshot_required()
    run = None
    parent_id = None
    tool_continuations: tuple[ToolContinuation, ...] = ()
    visibility = ChildVisibility(
        allow_legacy_details=False, tool_call_ids=frozenset(), namespaces=frozenset()
    )
    artifacts: frozenset[tuple[str, str]] = frozenset()
    if physical_id is not None:
        run = await require_read_run(
            db, conversation=conversation, context=context, run_id=physical_id
        )
        visibility = await load_child_visibility(db, conversation, context, physical_id)
        if visibility.allow_legacy_details and run.status == "interrupted":
            visibility = await constrain_legacy_child_visibility(
                db, conversation, context, run, visibility
            )
        artifacts = await readable_artifact_versions(
            db, context=context, conversation_id=conversation.id, run_id=physical_id
        )
        if binding is None and isinstance(run.metadata_json, dict):
            checkpoint = run.metadata_json.get("checkpoint_id")
            if isinstance(checkpoint, str):
                checkpoint_id = checkpoint
        if conversation.source == INDEPENDENT_JOB_SOURCE and run.source == "start":
            parent_id = await spawned_parent_public_id(db, conversation, context, run)
            if parent_id is None:
                # The spawn proof may have observed a grant withdrawal after our
                # first run check. Recheck before disclosing even the child alone.
                await require_read_run(
                    db, conversation=conversation, context=context, run_id=run.id
                )
        if run.source == "resume":
            tool_continuations = await read_tool_continuations(
                db,
                conversation=conversation,
                context=context,
                run=run,
                checkpoint_id=checkpoint_id,
                cache=continuation_cache,
            )
    return ReadTarget(
        public_id=public_id,
        run=run,
        input_id=binding.input_id if binding else None,
        input_status=binding.input_status if binding else None,
        position=binding.position if binding else None,
        revision=binding.revision if binding else None,
        version=version,
        parent_public_id=parent_id,
        scope=cursor_scope_for(
            conversation,
            public_id,
            physical_id,
            version,
            checkpoint_id,
            visibility,
            artifacts,
            parent_id,
            tool_continuations,
        ),
        artifacts=artifacts,
        delegated=conversation.source == INDEPENDENT_JOB_SOURCE,
        visibility=visibility,
        tool_continuations=tool_continuations,
    )
