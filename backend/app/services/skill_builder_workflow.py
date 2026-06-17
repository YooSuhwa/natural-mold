from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from langchain_core.messages import HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder.agent import build_skill_builder_model
from app.agent_runtime.skill_builder.graph import (
    HeuristicDraftWorker,
    JsonChatDraftWorker,
    SkillBuilderDraftWorker,
    run_skill_builder_graph,
)
from app.agent_runtime.skill_builder.state import SkillBuilderState
from app.agent_runtime.streaming import format_sse
from app.models.skill_builder_session import SkillBuilderSession
from app.schemas.skill_builder import JsonValue, SkillBuilderStatus
from app.services import skill_builder_service


@dataclass(frozen=True, slots=True)
class SkillBuilderSseEvent:
    event: str
    data: dict[str, JsonValue]


async def run_skill_builder_message_workflow(
    db: AsyncSession,
    *,
    session: SkillBuilderSession,
    user_id: uuid.UUID,
    content: str,
    draft_worker: SkillBuilderDraftWorker | None = None,
) -> list[SkillBuilderSseEvent]:
    worker = draft_worker
    if worker is None:
        worker = JsonChatDraftWorker(
            await build_skill_builder_model(db),
            fallback=HeuristicDraftWorker(),
        )
    await skill_builder_service.append_message(db, session, role="user", content=content)
    result = await run_skill_builder_graph(
        state={
            "messages": [HumanMessage(content=content)],
            "user_id": str(user_id),
            "session_id": str(session.id),
            "mode": session.mode,
            "source_skill_id": str(session.source_skill_id) if session.source_skill_id else None,
            "base_snapshot": session.base_snapshot,
            "user_request": _current_user_request(session, content),
            "current_phase": session.current_phase,
        },
        draft_worker=worker,
    )
    await _persist_result(db, session=session, result=result)
    await skill_builder_service.append_message(
        db,
        session,
        role="assistant",
        content=str(result.get("review_message") or ""),
    )
    await db.commit()
    await db.refresh(session)
    return _events(session_id=session.id, result=result)


async def stream_skill_builder_events(events: list[SkillBuilderSseEvent]):
    for item in events:
        yield format_sse(item.event, item.data)


async def _persist_result(
    db: AsyncSession,
    *,
    session: SkillBuilderSession,
    result: SkillBuilderState,
) -> None:
    session.intent = cast(dict[str, JsonValue] | None, result.get("intent"))
    session.draft_package = cast(dict[str, JsonValue] | None, result.get("draft_package"))
    session.validation_result = cast(dict[str, JsonValue] | None, result.get("validation_result"))
    session.compatibility_result = cast(
        dict[str, JsonValue] | None,
        result.get("compatibility_result"),
    )
    session.changelog_draft = cast(dict[str, JsonValue] | None, result.get("changelog_draft"))
    session.current_phase = int(result.get("current_phase") or session.current_phase)
    session.status = SkillBuilderStatus.REVIEW.value
    session.updated_at = datetime.now(UTC).replace(tzinfo=None)
    await db.flush()


def _events(session_id: uuid.UUID, result: SkillBuilderState) -> list[SkillBuilderSseEvent]:
    review_message = str(result.get("review_message") or "")
    events = [
        SkillBuilderSseEvent(
            "message_start",
            {"id": f"skill_builder_{session_id}", "role": "assistant"},
        ),
        SkillBuilderSseEvent(
            "builder_status",
            {"session_id": str(session_id), "status": "pending", "phase": "collect_intent"},
        ),
        SkillBuilderSseEvent(
            "builder_status",
            {"session_id": str(session_id), "status": "running", "phase": "draft_package"},
        ),
        SkillBuilderSseEvent("draft_package", _draft_event_payload(result)),
        SkillBuilderSseEvent(
            "builder_activity",
            {"kind": "validation", "status": "running", "label": "Validating package"},
        ),
        SkillBuilderSseEvent(
            "validation_result",
            cast(dict[str, JsonValue], result.get("validation_result") or {}),
        ),
        SkillBuilderSseEvent(
            "compatibility_result",
            cast(dict[str, JsonValue], result.get("compatibility_result") or {}),
        ),
        SkillBuilderSseEvent(
            "builder_activity",
            {"kind": "validation", "status": "complete", "label": "Validation complete"},
        ),
        SkillBuilderSseEvent(
            "changelog_draft",
            cast(dict[str, JsonValue], result.get("changelog_draft") or {}),
        ),
        SkillBuilderSseEvent(
            "eval_result",
            cast(dict[str, JsonValue], result.get("eval_result") or {}),
        ),
        SkillBuilderSseEvent("content_delta", {"delta": review_message}),
        SkillBuilderSseEvent(
            "builder_status",
            {"session_id": str(session_id), "status": "complete", "phase": "review"},
        ),
        SkillBuilderSseEvent("message_end", {"usage": {}, "content": review_message}),
    ]
    return events


def _draft_event_payload(result: SkillBuilderState) -> dict[str, JsonValue]:
    draft = result.get("draft_package") or {}
    files = draft.get("files") if isinstance(draft, dict) else None
    if not isinstance(files, list):
        return {"file_count": 0, "files": []}
    return {
        "file_count": len(files),
        "files": [_safe_file_item(item) for item in files if isinstance(item, dict)],
    }


def _safe_file_item(item: dict[object, object]) -> dict[str, JsonValue]:
    path = item.get("path")
    role = item.get("role")
    return {
        "path": path if isinstance(path, str) else "",
        "role": role if isinstance(role, str) else "asset",
    }


def _current_user_request(session: SkillBuilderSession, content: str) -> str:
    if session.user_request.strip():
        return session.user_request
    return content
