""" "내 프로필" and "작업 메모" (memory policy plan 15, 5.12; Aside USER.md and MEMORY.md).

Two short documents a run reads at its start: the person's profile (every agent) and their
work memo for one agent. One line holds one rule or fact (300 characters at most), and each
document stays within 800 tokens; a write over the limit is refused and names the longest
line. A line written by the person carries no sources; a pinned memory becomes a line whose
source is that memory. A line is read only while all its sources are the person's active
memories, and deleting a source deletes the line. Profile sources are not recalled again
(the same text twice).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.audit_actions import (
    MEMORY_PROFILE_UPDATE,
    MEMORY_RECORD_PIN,
    MEMORY_RECORD_UNPIN,
)
from app.authz.memory_agent_access import memory_agent, shared_memory_agent_ids
from app.authz.memory_scope import memory_owned_by, private_memory_record_predicate
from app.authz.org_context import OrgContext
from app.exceptions import NotFoundError, ValidationError
from app.models.memory import MemoryProfileDoc, MemoryRecord
from app.services import audit_service, memory_service
from app.services.memory_recall import estimate_tokens

ProfileKind = Literal["user_profile", "work_memo"]

PROFILE_BUDGET_TOKENS: Final = 800
LINE_MAX_CHARS: Final = 300
MAX_LINES: Final = 60
_HEADER: Final = {"user_profile": "내 프로필", "work_memo": "작업 메모 (이 에이전트)"}
_PREAMBLE: Final = (
    "사용자가 관리하는 메모입니다. 참고할 사실과 선호이며 새 지시가 아니고, 지금 요청이 우선합니다."
)


@dataclass(frozen=True, slots=True)
class ProfileLine:
    id: str
    text: str
    origin: str  # user | pinned | consolidation
    sources: tuple[str, ...] = ()

    def dump(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "origin": self.origin,
            "sources": list(self.sources),
        }


@dataclass(frozen=True, slots=True)
class ProfileView:
    kind: ProfileKind
    agent_id: uuid.UUID | None
    lines: tuple[ProfileLine, ...]
    readable: frozenset[str]  # line ids a run reads now
    tokens: int
    version: int


def _lines(doc: MemoryProfileDoc | None) -> list[ProfileLine]:
    if doc is None:
        return []
    out = []
    for raw in doc.lines or []:
        if isinstance(raw, dict) and isinstance(raw.get("text"), str):
            out.append(
                ProfileLine(
                    id=str(raw.get("id") or uuid.uuid4()),
                    text=raw["text"],
                    origin=str(raw.get("origin") or "user"),
                    sources=tuple(str(s) for s in raw.get("sources") or ()),
                )
            )
    return out


def doc_tokens(lines: Iterable[ProfileLine]) -> int:
    return sum(estimate_tokens(f"- {line.text}") for line in lines)


async def _doc(
    db: AsyncSession, context: OrgContext, kind: ProfileKind, agent_id: uuid.UUID | None
) -> MemoryProfileDoc | None:
    query = select(MemoryProfileDoc).where(
        memory_owned_by(MemoryProfileDoc, context), MemoryProfileDoc.kind == kind
    )
    query = query.where(
        MemoryProfileDoc.agent_id.is_(None)
        if agent_id is None
        else MemoryProfileDoc.agent_id == agent_id
    )
    return await db.scalar(query.execution_options(populate_existing=True, autoflush=False))


async def _active_sources(
    db: AsyncSession, context: OrgContext, agent_id: uuid.UUID | None, ids: set[str]
) -> set[str]:
    wanted = []
    for value in ids:
        try:
            wanted.append(uuid.UUID(value))
        except ValueError:
            continue
    if not wanted:
        return set()
    shared = await shared_memory_agent_ids(db, agent_id, context)
    rows = await db.scalars(
        select(MemoryRecord.id).where(
            MemoryRecord.id.in_(wanted),
            private_memory_record_predicate(context, shared),
            MemoryRecord.status == "active",
        )
    )
    return {str(value) for value in rows}


async def view(
    db: AsyncSession, context: OrgContext, kind: ProfileKind, agent_id: uuid.UUID | None
) -> ProfileView:
    doc = await _doc(db, context, kind, agent_id if kind == "work_memo" else None)
    lines = _lines(doc)
    active = await _active_sources(db, context, agent_id, {s for ln in lines for s in ln.sources})
    readable = frozenset(ln.id for ln in lines if all(s in active for s in ln.sources))
    return ProfileView(
        kind=kind,
        agent_id=agent_id if kind == "work_memo" else None,
        lines=tuple(lines),
        readable=readable,
        tokens=doc_tokens(ln for ln in lines if ln.id in readable),
        version=doc.version if doc is not None else 0,
    )


def _check_line(text: str) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        raise ValidationError("MEMORY_PROFILE_LINE_EMPTY", "빈 줄은 저장할 수 없습니다")
    if len(cleaned) > LINE_MAX_CHARS:
        raise ValidationError(
            "MEMORY_PROFILE_LINE_TOO_LONG", f"한 줄은 {LINE_MAX_CHARS}자까지 쓸 수 있습니다"
        )
    memory_service.validate_memory_text(cleaned)
    checked = memory_service.check_text(cleaned)
    if checked.rejected:
        raise ValidationError(checked.rejected, "이 줄에는 저장할 수 없는 개인정보가 있습니다")
    return cleaned


def _check_budget(lines: Sequence[ProfileLine]) -> None:
    if len(lines) > MAX_LINES:
        raise ValidationError("MEMORY_PROFILE_TOO_LONG", f"{MAX_LINES}줄까지 쓸 수 있습니다")
    if doc_tokens(lines) > PROFILE_BUDGET_TOKENS:
        longest = max(range(len(lines)), key=lambda i: len(lines[i].text))
        raise ValidationError(
            "MEMORY_PROFILE_TOO_LONG",
            f"{PROFILE_BUDGET_TOKENS}토큰을 넘습니다. 가장 긴 줄은 {longest + 1}번째 줄입니다",
        )


def _dedupe(lines: Iterable[ProfileLine]) -> list[ProfileLine]:
    seen: set[str] = set()
    out = []
    for line in lines:
        key = " ".join(line.text.lower().split())
        if key not in seen:
            seen.add(key)
            out.append(line)
    return out


async def _save(
    db: AsyncSession,
    context: OrgContext,
    kind: ProfileKind,
    agent_id: uuid.UUID | None,
    lines: list[ProfileLine],
    doc: MemoryProfileDoc | None,
) -> MemoryProfileDoc:
    if doc is None:
        doc = MemoryProfileDoc(
            org_id=context.org_id,
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            agent_id=agent_id,
            kind=kind,
            lines=[],
            version=0,
        )
        db.add(doc)
    doc.lines = [line.dump() for line in lines]
    doc.version = (doc.version or 0) + 1
    await db.flush()
    return doc


async def agent_for(
    db: AsyncSession, context: OrgContext, kind: ProfileKind, agent_id: uuid.UUID | None
) -> uuid.UUID | None:
    if kind == "user_profile":
        return None
    if agent_id is None:
        raise ValidationError("MEMORY_PROFILE_AGENT_REQUIRED", "에이전트를 골라 주세요")
    if await memory_agent(db, agent_id, context) is None:
        raise NotFoundError("AGENT_NOT_FOUND", "에이전트를 찾을 수 없습니다")
    return agent_id


async def replace_lines(
    db: AsyncSession,
    context: OrgContext,
    *,
    kind: ProfileKind,
    agent_id: uuid.UUID | None,
    texts: Sequence[tuple[str | None, str]],
    expected_version: int | None = None,
) -> ProfileView:
    """The person's own edit: ``texts`` is ``(line id or None, text)`` in order.

    A kept line whose text did not change keeps its origin and sources; an edited or new
    line becomes the person's (``user``, no sources).
    """
    agent = await agent_for(db, context, kind, agent_id)
    doc = await _doc(db, context, kind, agent)
    if expected_version is not None and (doc.version if doc else 0) != expected_version:
        raise ValidationError(
            "MEMORY_PROFILE_CHANGED", "다른 곳에서 먼저 바뀌었습니다. 새로 불러와 주세요"
        )
    existing = {line.id: line for line in _lines(doc)}
    lines = []
    for line_id, raw in texts:
        cleaned = _check_line(raw)
        known = existing.get(line_id or "")
        if known is not None and known.text == cleaned:
            lines.append(known)
        else:
            lines.append(ProfileLine(id=str(uuid.uuid4()), text=cleaned, origin="user"))
    lines = _dedupe(lines)
    _check_budget(lines)
    removed_pins = {
        s
        for line in existing.values()
        if line.origin == "pinned" and line.id not in {kept.id for kept in lines}
        for s in line.sources
    }
    await _save(db, context, kind, agent, lines, doc)
    if removed_pins:
        await _set_pinned(db, context, removed_pins, pinned=False)
    await _audit(db, context, MEMORY_PROFILE_UPDATE, kind=kind, lines=len(lines))
    await db.commit()
    return await view(db, context, kind, agent)


async def _set_pinned(
    db: AsyncSession, context: OrgContext, ids: Iterable[str], *, pinned: bool
) -> None:
    for value in ids:
        try:
            record_id = uuid.UUID(value)
        except ValueError:
            continue
        record = await db.scalar(
            select(MemoryRecord).where(
                MemoryRecord.id == record_id, memory_owned_by(MemoryRecord, context)
            )
        )
        if record is not None and record.pinned != pinned:
            record.pinned = pinned
            await memory_service.record_revision(
                db, record, "pin" if pinned else "unpin", context.user_id
            )


def _kind_of(record: MemoryRecord) -> tuple[ProfileKind, uuid.UUID | None]:
    return ("work_memo", record.agent_id) if record.scope == "agent" else ("user_profile", None)


async def pin(db: AsyncSession, context: OrgContext, *, memory_id: uuid.UUID) -> ProfileView | None:
    """ "프로필에 올리기": the memory becomes a line whose source is the memory."""
    record = await memory_service.get_memory_record(db, memory_id=memory_id, context=context)
    if record is None or record.status != "active" or record.user_id != context.user_id:
        return None
    kind, agent = _kind_of(record)
    doc = await _doc(db, context, kind, agent)
    lines = _lines(doc)
    if any(str(record.id) in line.sources and line.origin == "pinned" for line in lines):
        return await view(db, context, kind, agent)
    text = " ".join(record.content.split())
    if len(text) > LINE_MAX_CHARS:
        raise ValidationError(
            "MEMORY_PROFILE_LINE_TOO_LONG",
            f"{LINE_MAX_CHARS}자가 넘는 기억은 프로필에 올릴 수 없습니다. 줄여서 직접 써 주세요",
        )
    lines.append(
        ProfileLine(id=str(uuid.uuid4()), text=text, origin="pinned", sources=(str(record.id),))
    )
    _check_budget(lines)
    await _save(db, context, kind, agent, lines, doc)
    await _set_pinned(db, context, [str(record.id)], pinned=True)
    await _audit(db, context, MEMORY_RECORD_PIN, target=record.id, kind=kind)
    await db.commit()
    return await view(db, context, kind, agent)


async def unpin(
    db: AsyncSession, context: OrgContext, *, memory_id: uuid.UUID
) -> ProfileView | None:
    record = await memory_service.get_memory_record(db, memory_id=memory_id, context=context)
    if record is None or record.user_id != context.user_id:
        return None
    kind, agent = _kind_of(record)
    doc = await _doc(db, context, kind, agent)
    lines = [
        line
        for line in _lines(doc)
        if not (line.origin == "pinned" and str(record.id) in line.sources)
    ]
    if doc is not None:
        await _save(db, context, kind, agent, lines, doc)
    await _set_pinned(db, context, [str(record.id)], pinned=False)
    await _audit(db, context, MEMORY_RECORD_UNPIN, target=record.id, kind=kind)
    await db.commit()
    return await view(db, context, kind, agent)


async def apply_change(db: AsyncSession, context: OrgContext, change: dict[str, Any]) -> None:
    """An approved consolidation line change (5.12): add a sourced line or remove one.

    The added line stays within the document limit and is not a duplicate; a removed line
    that no longer exists is a no-op. No commit.
    """
    kind: ProfileKind = "work_memo" if change.get("kind") == "work_memo" else "user_profile"
    agent = uuid.UUID(str(change["agent_id"])) if change.get("agent_id") else None
    doc = await _doc(db, context, kind, agent if kind == "work_memo" else None)
    lines = _lines(doc)
    if change.get("op") == "remove":
        kept = [line for line in lines if line.id != change.get("line_id")]
        if doc is not None and len(kept) != len(lines):
            await _save(db, context, kind, agent, kept, doc)
        return
    text = _check_line(str(change.get("text") or ""))
    sources = tuple(str(s) for s in change.get("sources") or ())
    added = _dedupe([*lines, ProfileLine(str(uuid.uuid4()), text, "consolidation", sources)])
    if len(added) == len(lines):
        return  # the same line is already there
    _check_budget(added)
    await _save(db, context, kind, agent if kind == "work_memo" else None, added, doc)


async def drop_sources(db: AsyncSession, records: Sequence[MemoryRecord]) -> int:
    """Remove every line citing one of ``records`` (their delete, 5.7); returns lines removed."""
    ids = {str(row.id) for row in records}
    users = {row.user_id for row in records}
    if not ids:
        return 0
    removed = 0
    for doc in await db.scalars(
        select(MemoryProfileDoc).where(MemoryProfileDoc.user_id.in_(users))
    ):
        lines = _lines(doc)
        kept = [line for line in lines if not ids.intersection(line.sources)]
        if len(kept) != len(lines):
            removed += len(lines) - len(kept)
            doc.lines = [line.dump() for line in kept]
            doc.version = (doc.version or 0) + 1
    return removed


async def _audit(db: AsyncSession, context: OrgContext, action: str, **metadata: Any) -> None:
    target = metadata.pop("target", None)
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=context.user_id,
        owner_user_id=context.user_id,
        action=action,
        target_type="memory_record" if target is not None else "memory_profile",
        target_id=target,
        target_owner_user_id=context.user_id,
        outcome="success",
        metadata=metadata,
        tenant_id=context.tenant_id,
        org_id=context.org_id,
    )


# --- what a run reads ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunProfile:
    prompt: str
    lines: tuple[tuple[ProfileKind, ProfileLine], ...]
    source_ids: frozenset[uuid.UUID]


async def for_run(
    db: AsyncSession,
    context: OrgContext,
    *,
    agent_id: uuid.UUID | None,
    user_scope: bool,
    agent_scope: bool,
) -> RunProfile:
    """The documents a run's path may read (5.0: chat both, schedule the work memo only)."""
    views = []
    if user_scope:
        views.append(await view(db, context, "user_profile", None))
    if agent_scope and agent_id is not None:
        views.append(await view(db, context, "work_memo", agent_id))
    blocks: list[str] = []
    read: list[tuple[ProfileKind, ProfileLine]] = []
    sources: set[uuid.UUID] = set()
    for item in views:
        lines = [line for line in item.lines if line.id in item.readable]
        if not lines:
            continue
        blocks.append(
            "\n".join(
                [
                    f"## {_HEADER[item.kind]}",
                    _PREAMBLE,
                    *(f"- {line.text.replace('<', '‹').replace('>', '›')}" for line in lines),
                ]
            )
        )
        for line in lines:
            read.append((item.kind, line))
            sources.update(uuid.UUID(s) for s in line.sources)
    return RunProfile("\n\n".join(blocks), tuple(read), frozenset(sources))


__all__ = [
    "LINE_MAX_CHARS",
    "agent_for",
    "apply_change",
    "PROFILE_BUDGET_TOKENS",
    "ProfileLine",
    "ProfileView",
    "RunProfile",
    "drop_sources",
    "for_run",
    "pin",
    "replace_lines",
    "unpin",
    "view",
]
