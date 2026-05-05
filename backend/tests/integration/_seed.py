"""Integration test 공용 시드 헬퍼.

W3-out 이전엔 ``test_stream_resume.py`` 의 ``_seed_conv`` 와
``test_broker_dual_write.py`` 의 ``_seed`` 가 거의 동일한 User+Model+Agent+
Conversation 시퀀스를 hand-roll. 한 곳으로 통합하면 향후 모델/스키마 변경
시 한 파일만 수정하면 된다 (W3-out retrospective MEDIUM follow-up 정리).

신규 통합 테스트가 같은 패턴을 필요로 하면 본 헬퍼를 그대로 재사용.
"""

from __future__ import annotations

import uuid

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.user import User
from tests.conftest import TEST_USER_ID, TestSession


async def seed_conversation_with_agent(
    *,
    agent_name: str = "Test Agent",
    conv_title: str = "Test Conv",
    system_prompt: str = "x",
    model_provider: str = "openai",
    model_name: str = "gpt-4o",
    model_display_name: str = "GPT-4o",
) -> uuid.UUID:
    """User + Model + Agent + Conversation 시드 한 줄. 새 ``conversation.id`` 반환.

    User row 는 idempotent — autouse fixture 가 schema 만 만들고 row 는 안
    채우므로, 같은 ``TEST_USER_ID`` 로 중복 호출 시 second insert 가 unique
    제약 위반이 안 나도록 미리 존재 검사. Model/Agent/Conversation 은 매번
    fresh row.
    """
    async with TestSession() as db:
        existing = await db.get(User, TEST_USER_ID)
        if existing is None:
            db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test"))
        model = Model(
            provider=model_provider,
            model_name=model_name,
            display_name=model_display_name,
        )
        db.add(model)
        await db.flush()
        agent = Agent(
            user_id=TEST_USER_ID,
            name=agent_name,
            system_prompt=system_prompt,
            model_id=model.id,
        )
        db.add(agent)
        await db.flush()
        conv = Conversation(agent_id=agent.id, title=conv_title)
        db.add(conv)
        await db.commit()
        return conv.id
