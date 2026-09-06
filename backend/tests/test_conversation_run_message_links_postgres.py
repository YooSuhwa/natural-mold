from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.message_event import MessageEvent
from app.services import conversation_run_service
from app.services.conversation_run_message_links import RunMessageLinkQuery, list_run_message_links
from tests.test_conversation_run_message_links import _create_run, _seed_conversation


@pytest.mark.asyncio
@pytest.mark.integration
async def test_postgres_json_projection_omits_ambiguous_and_retains_same_run_link() -> None:
    raw_url = os.environ.get("INTEGRATION_DATABASE_URL")
    if raw_url is None:
        pytest.skip("INTEGRATION_DATABASE_URL is required")
    parsed_url = make_url(raw_url)
    if parsed_url.database != "moldy_pg_lane_run_links":
        pytest.fail("run-link PostgreSQL test requires its dedicated disposable database")
    engine = create_async_engine(parsed_url.set(drivername="postgresql+asyncpg"))
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as db:
            owner_id = uuid.uuid4()
            agent, conversation = await _seed_conversation(
                db,
                user_id=owner_id,
                email=f"run-links-{owner_id.hex}@test.local",
            )
            older = await _create_run(db, agent=agent, conversation=conversation)
            await conversation_run_service.transition_run(db, older, "running")
            await conversation_run_service.transition_run(db, older, "completed")
            newer = await _create_run(db, agent=agent, conversation=conversation)
            ambiguous_message_id = uuid.uuid4()
            retained_message_id = uuid.uuid4()
            db.add_all(
                [
                    MessageEvent(
                        conversation_id=conversation.id,
                        assistant_msg_id=str(older.id),
                        linked_message_ids=[str(ambiguous_message_id)],
                        events=[],
                    ),
                    MessageEvent(
                        conversation_id=conversation.id,
                        assistant_msg_id=str(newer.id),
                        linked_message_ids=[
                            str(ambiguous_message_id),
                            str(retained_message_id),
                        ],
                        events=[],
                    ),
                    MessageEvent(
                        conversation_id=conversation.id,
                        assistant_msg_id=newer.id.hex,
                        linked_message_ids=[str(retained_message_id)],
                        events=[],
                    ),
                ]
            )
            await db.commit()
            links = await list_run_message_links(
                db,
                RunMessageLinkQuery(
                    conversation_id=conversation.id,
                    user_id=owner_id,
                    message_ids=(str(ambiguous_message_id), str(retained_message_id)),
                ),
            )
        assert [link.model_dump() for link in links] == [
            {"message_id": str(retained_message_id), "run_id": newer.id}
        ]
    finally:
        await engine.dispose()
