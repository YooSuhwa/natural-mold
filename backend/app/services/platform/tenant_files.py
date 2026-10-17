"""A company's files on disk: export entries and permanent-purge cleanup (I-07).

Collected from the company's rows before anything is deleted:

* conversations: checkpoints, internal offloads, attachment blobs and the local artifact
  tree, removed through the retention cleanup (``retention.conversations.clean_up_files``);
* attachments staged without a conversation;
* agents: the avatar directory and the runtime ``agents/<id>`` directory;
* skills: the company skill's own storage directory (only a directory named after the
  skill id, so a shared or system package directory is never touched);
* finished export archives.

Removal is idempotent and best effort (a failure is logged); the purge removes files
before rows, so a rerun after a failure finds the same targets again.
"""

import logging
import shutil
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from anyio.to_thread import run_sync
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_config import runtime_data_dir
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message_attachment import MessageAttachment
from app.models.skill import Skill
from app.models.tenant_export import TenantExport
from app.services.agent_image_paths import agent_image_dir
from app.services.retention.conversations import (
    DeletedConversation,
    _artifact_root,
    clean_up_files,
)
from app.storage.paths import resolve_data_path

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CompanyFiles:
    conversations: list[DeletedConversation] = field(default_factory=list)
    attachments: list[tuple[UUID, Path]] = field(default_factory=list)  # every attachment
    agent_dirs: list[tuple[str, Path]] = field(default_factory=list)  # (archive name, dir)
    skill_dirs: list[tuple[UUID, Path]] = field(default_factory=list)
    exports: list[Path] = field(default_factory=list)


def files_to_json(files: CompanyFiles) -> dict[str, list[list[str]]]:
    """The cleanup targets as JSON (kept on the purge receipt after the rows are gone)."""
    return {
        "conversations": [
            [str(c.conversation_id), str(c.owner_id), *c.attachment_paths]
            for c in files.conversations
        ],
        "attachments": [[str(i), str(p)] for i, p in files.attachments],
        "agent_dirs": [[name, str(p)] for name, p in files.agent_dirs],
        "skill_dirs": [[str(i), str(p)] for i, p in files.skill_dirs],
        "exports": [[str(p)] for p in files.exports],
    }


def files_from_json(raw: Mapping[str, Any]) -> CompanyFiles:
    def rows(key: str) -> list[list[str]]:
        return [list(map(str, row)) for row in raw.get(key, [])]

    return CompanyFiles(
        conversations=[
            DeletedConversation(UUID(row[0]), UUID(row[1]), tuple(row[2:]))
            for row in rows("conversations")
        ],
        attachments=[(UUID(i), Path(p)) for i, p in rows("attachments")],
        agent_dirs=[(name, Path(p)) for name, p in rows("agent_dirs")],
        skill_dirs=[(UUID(i), Path(p)) for i, p in rows("skill_dirs")],
        exports=[Path(row[0]) for row in rows("exports")],
    )


def local_path(raw: str) -> Path | None:
    try:
        return Path(raw) if Path(raw).is_absolute() else resolve_data_path(raw)
    except ValueError:
        return None


def _skill_dir(skill_id: UUID, storage_path: str | None) -> Path | None:
    path = local_path(storage_path) if storage_path else None
    if path is None:
        return None
    target = path.parent if path.suffix else path  # text skills: .../<id>/SKILL.md
    return target if target.name == str(skill_id) else None


async def company_files(db: AsyncSession, tenant_id: UUID) -> CompanyFiles:
    found = CompanyFiles()
    attachments = (
        await db.execute(
            select(
                MessageAttachment.id,
                MessageAttachment.conversation_id,
                MessageAttachment.storage_path,
            ).where(MessageAttachment.tenant_id == tenant_id)
        )
    ).all()
    by_conversation: dict[UUID, list[str]] = {}
    for row in attachments:
        if (path := local_path(row.storage_path)) is not None:
            found.attachments.append((row.id, path))
        if row.conversation_id is not None:
            by_conversation.setdefault(row.conversation_id, []).append(row.storage_path)
    conversations = await db.execute(
        select(Conversation.id, Conversation.user_id).where(Conversation.tenant_id == tenant_id)
    )
    for conversation_id, owner_id in conversations.all():
        paths = tuple(by_conversation.get(conversation_id, ()))
        found.conversations.append(DeletedConversation(conversation_id, owner_id, paths))
    for agent_id in await db.scalars(select(Agent.id).where(Agent.tenant_id == tenant_id)):
        found.agent_dirs.append((f"agent_images/{agent_id}", agent_image_dir() / str(agent_id)))
        found.agent_dirs.append(
            (f"agents/{agent_id}", runtime_data_dir() / "agents" / str(agent_id))
        )
    skills = await db.execute(
        select(Skill.id, Skill.storage_path).where(Skill.tenant_id == tenant_id)
    )
    for skill_id, storage_path in skills.all():
        if (path := _skill_dir(skill_id, storage_path)) is not None:
            found.skill_dirs.append((skill_id, path))
    exports = await db.scalars(
        select(TenantExport.storage_path).where(
            TenantExport.tenant_id == tenant_id, TenantExport.storage_path.is_not(None)
        )
    )
    found.exports = [path for raw in exports if raw and (path := local_path(raw)) is not None]
    return found


def export_entries(files: CompanyFiles) -> Iterator[tuple[str, Path]]:
    """``(archive name, file)`` for every regular file the export carries."""
    for attachment_id, path in files.attachments:
        yield f"files/attachments/{attachment_id}/{path.name}", path
    roots: list[tuple[str, Path | None]] = [
        (f"files/artifacts/{item.conversation_id}", _artifact_root(item.conversation_id))
        for item in files.conversations
    ]
    roots += [(f"files/{name}", path) for name, path in files.agent_dirs]
    roots += [(f"files/skills/{skill_id}", path) for skill_id, path in files.skill_dirs]
    for prefix, root in roots:
        if root is None or not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                yield f"{prefix}/{path.relative_to(root).as_posix()}", path


def _remove(path: Path) -> None:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)
    except OSError:
        logger.warning("tenant purge file delete failed: %s", path, exc_info=True)


def _remove_all(paths: list[Path]) -> None:
    for path in paths:
        _remove(path)


def export_root(tenant_id: UUID) -> Path | None:
    return local_path(f"tenant_exports/{tenant_id}")


async def remove_company_files(files: CompanyFiles) -> None:
    for item in files.conversations:
        await clean_up_files(item)  # checkpoints, offloads, attachments, artifact tree
    paths = [path for _, path in files.attachments]
    paths += [path for _, path in files.agent_dirs]
    paths += [path for _, path in files.skill_dirs]
    paths += files.exports
    await run_sync(_remove_all, paths)
