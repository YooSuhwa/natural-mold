from typing import Literal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.message_attachment import MessageAttachment
from app.services.ag_ui_start_effects import authorize_start_attachments
from tests.authz.execution_gateway_testkit import GatewayGraph, seed_graph


def orphan(graph: GatewayGraph) -> MessageAttachment:
    context = graph.context
    return MessageAttachment(
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        filename="upload.txt",
        mime_type="text/plain",
        size_bytes=1,
        storage_path="unused",
        url="/unused",
    )


@pytest.mark.parametrize("scope", ["missing", "foreign", "other_conversation", "allowed"])
async def test_all_ag_ui_uploads_must_belong_to_the_private_conversation(
    db: AsyncSession, scope: Literal["missing", "foreign", "other_conversation", "allowed"]
) -> None:
    graph = await seed_graph(db)
    first, other = graph.conversation_ids
    available = orphan(graph)
    target = orphan(await seed_graph(db) if scope == "foreign" else graph)
    if scope in {"other_conversation", "allowed"}:
        target.conversation_id = other if scope == "other_conversation" else first
    db.add_all([available, target])
    await db.commit()
    ids = [available.id, uuid4() if scope == "missing" else target.id]
    if scope == "allowed":
        await authorize_start_attachments(db, graph.context, first, ids)
        with pytest.raises(AppError) as unbound:
            await authorize_start_attachments(db, graph.context, first, ids, require_linked=True)
        assert unbound.value.code == "FILE_NOT_FOUND"
        await authorize_start_attachments(
            db, graph.context, first, [target.id], require_linked=True
        )
    else:
        with pytest.raises(AppError) as hidden:
            await authorize_start_attachments(db, graph.context, first, ids)
        assert (hidden.value.status, hidden.value.code) == (404, "FILE_NOT_FOUND")
    await db.refresh(available)
    assert available.conversation_id is None
