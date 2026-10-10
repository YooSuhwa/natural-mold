"""Resolve actual private conversation owners before default-organization backfill."""

from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict
from sqlalchemy.engine import Connection


class ConversationSource(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    user_id: UUID | None
    agent_owner: UUID | None
    runtime_profile: str | None
    parent_id: UUID | None


def unique_owners(connection: Connection, source: sa.Table) -> dict[UUID, UUID]:
    owners: dict[UUID, UUID] = {}
    rows = connection.execute(sa.select(source.c.conversation_id, source.c.user_id)).all()
    for raw in rows:
        conversation, owner = raw
        if conversation is None:
            continue
        if conversation in owners and owners[conversation] != owner:
            raise ValueError(f"Ambiguous conversation owner in {source.name}: {conversation}")
        owners[conversation] = owner
    return owners


def resolve_conversations(connection: Connection, metadata: sa.MetaData) -> None:
    conversations = metadata.tables["conversations"]
    agents = metadata.tables["agents"]
    api = unique_owners(connection, metadata.tables["agent_api_threads"])
    hidden = unique_owners(connection, metadata.tables["skill_builder_sessions"])
    rows = connection.execute(
        sa.select(
            conversations.c.id,
            conversations.c.user_id,
            agents.c.user_id.label("agent_owner"),
            agents.c.runtime_profile,
            conversations.c.side_chat_parent_id.label("parent_id"),
        ).select_from(conversations.outerjoin(agents, conversations.c.agent_id == agents.c.id))
    ).mappings()
    pending = {row.id: row for row in (ConversationSource.model_validate(raw) for raw in rows)}
    owners = {key: row.user_id for key, row in pending.items() if row.user_id is not None}
    while pending:
        resolved: list[UUID] = []
        for key, row in pending.items():
            owner = owners.get(key) or api.get(key)
            if owner is None and row.parent_id is not None:
                owner = owners.get(row.parent_id)
            elif owner is None and row.runtime_profile == "skill_builder":
                owner = hidden.get(key)
            elif owner is None and row.runtime_profile == "standard":
                owner = row.agent_owner
            if owner is None:
                continue
            owners[key] = owner
            connection.execute(
                sa.update(conversations)
                .where(conversations.c.id == key, conversations.c.user_id.is_(None))
                .values(user_id=owner)
            )
            resolved.append(key)
        if not resolved:
            unresolved = ", ".join(str(key) for key in pending)
            raise ValueError(f"Cannot resolve conversation owners: {unresolved}")
        for key in resolved:
            del pending[key]
