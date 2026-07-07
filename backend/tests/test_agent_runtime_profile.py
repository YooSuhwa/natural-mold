"""히든 런타임 에이전트(runtime_profile != 'standard') 노출 차단 계약 (스펙 AD-1).

M1 전수 grep으로 확정한 표면: 에이전트 목록/요약, 채팅 네비게이터,
usage per-agent breakdown, 일일 집계(agent축), Agent API 배포 후보,
서브에이전트 연결 검증. 변조(PUT/DELETE)는 enumeration-safe 404.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import (
    AGENT_RUNTIME_PROFILE_SKILL_BUILDER,
    AGENT_RUNTIME_PROFILE_STANDARD,
    Agent,
)
from app.models.conversation import Conversation
from app.models.daily_spend_agent import DailySpendAgent
from app.models.model import Model
from app.services.usage_aggregate import get_daily_spend
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio


async def _seed_agents(db: AsyncSession) -> tuple[Agent, Agent]:
    """표준 에이전트 1 + 히든(skill_builder) 에이전트 1을 만든다."""

    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()

    standard = Agent(
        user_id=TEST_USER_ID,
        name="Visible Agent",
        system_prompt="You are visible.",
        model_id=model.id,
    )
    hidden = Agent(
        user_id=TEST_USER_ID,
        name="Hidden Builder",
        system_prompt="You are hidden.",
        model_id=model.id,
        runtime_profile=AGENT_RUNTIME_PROFILE_SKILL_BUILDER,
    )
    db.add_all([standard, hidden])
    await db.commit()
    await db.refresh(standard)
    await db.refresh(hidden)
    return standard, hidden


async def test_default_runtime_profile_is_standard(db: AsyncSession) -> None:
    standard, hidden = await _seed_agents(db)
    assert standard.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD
    assert hidden.runtime_profile == AGENT_RUNTIME_PROFILE_SKILL_BUILDER


async def test_agent_list_and_summary_exclude_hidden(
    client: AsyncClient, db: AsyncSession
) -> None:
    standard, hidden = await _seed_agents(db)

    listed = await client.get("/api/agents")
    assert listed.status_code == 200
    ids = {row["id"] for row in listed.json()}
    assert str(standard.id) in ids
    assert str(hidden.id) not in ids

    summary = await client.get("/api/agents/summary")
    assert summary.status_code == 200
    summary_ids = {row["id"] for row in summary.json()}
    assert str(standard.id) in summary_ids
    assert str(hidden.id) not in summary_ids


async def test_get_single_hidden_agent_still_readable(
    client: AsyncClient, db: AsyncSession
) -> None:
    """빌더 챗 서피스가 에이전트 메타를 읽어야 하므로 GET 단건은 열어 둔다."""

    _, hidden = await _seed_agents(db)

    response = await client.get(f"/api/agents/{hidden.id}")
    assert response.status_code == 200
    assert response.json()["id"] == str(hidden.id)


async def test_put_delete_hidden_agent_return_404(
    client: AsyncClient, db: AsyncSession
) -> None:
    standard, hidden = await _seed_agents(db)

    put = await client.put(f"/api/agents/{hidden.id}", json={"name": "tampered"})
    assert put.status_code == 404

    delete = await client.delete(f"/api/agents/{hidden.id}")
    assert delete.status_code == 404

    # 존재하지 않는 id와 응답이 동일해야 enumeration-safe.
    missing = await client.put(f"/api/agents/{uuid.uuid4()}", json={"name": "x"})
    assert missing.status_code == 404
    assert missing.json() == put.json()

    # 히든 row는 그대로 살아 있고, 표준 에이전트는 정상 변조 가능.
    survivor = await db.get(Agent, hidden.id)
    assert survivor is not None and survivor.name == "Hidden Builder"
    ok = await client.put(f"/api/agents/{standard.id}", json={"name": "renamed"})
    assert ok.status_code == 200


async def test_navigator_excludes_hidden_agent_conversations(
    client: AsyncClient, db: AsyncSession
) -> None:
    standard, hidden = await _seed_agents(db)
    db.add_all(
        [
            Conversation(agent_id=standard.id, title="visible talk", source="ui"),
            Conversation(agent_id=hidden.id, title="builder talk", source="ui"),
        ]
    )
    await db.commit()

    page = await client.get("/api/conversations/page", params={"limit": 50})
    assert page.status_code == 200
    titles = {item["title"] for item in page.json()["items"]}
    assert "visible talk" in titles
    assert "builder talk" not in titles


async def test_usage_summary_by_agent_excludes_hidden(
    client: AsyncClient, db: AsyncSession
) -> None:
    standard, hidden = await _seed_agents(db)
    today = datetime.now(UTC).date()
    db.add_all(
        [
            DailySpendAgent(
                date=today, agent_id=standard.id, total_tokens_in=10, total_tokens_out=5
            ),
            DailySpendAgent(
                date=today, agent_id=hidden.id, total_tokens_in=100, total_tokens_out=50
            ),
        ]
    )
    await db.commit()

    summary = await client.get("/api/usage/summary")
    assert summary.status_code == 200
    by_agent_ids = {row["agent_id"] for row in summary.json()["by_agent"]}
    assert str(standard.id) in by_agent_ids
    assert str(hidden.id) not in by_agent_ids


async def test_daily_spend_agent_axis_excludes_hidden(db: AsyncSession) -> None:
    standard, hidden = await _seed_agents(db)
    day = date(2026, 7, 1)
    db.add_all(
        [
            DailySpendAgent(
                date=day, agent_id=standard.id, total_tokens_in=10, total_tokens_out=5
            ),
            DailySpendAgent(
                date=day, agent_id=hidden.id, total_tokens_in=100, total_tokens_out=50
            ),
        ]
    )
    await db.commit()

    rows = await get_daily_spend(
        db,
        user_id=TEST_USER_ID,
        target_kind="agent",
        from_date=day,
        to_date=day,
        group_by="target",
    )
    # aiosqlite는 Uuid 컬럼을 raw select에서 문자열로 돌려줄 수 있어 str로 통일.
    target_ids = {str(row["target_id"]) for row in rows}
    assert str(standard.id) in target_ids
    assert str(hidden.id) not in target_ids

    # date축도 히든 spend를 합산하지 않는다 (축 간 정합).
    date_rows = await get_daily_spend(
        db,
        user_id=TEST_USER_ID,
        target_kind="agent",
        from_date=day,
        to_date=day,
        group_by="date",
    )
    assert len(date_rows) == 1
    assert date_rows[0]["total_tokens_in"] == 10


async def test_deployment_candidates_exclude_hidden(
    client: AsyncClient, db: AsyncSession
) -> None:
    standard, hidden = await _seed_agents(db)

    response = await client.get("/api/agent-api/deployment-candidates")
    assert response.status_code == 200
    candidate_ids = {row["agent_id"] for row in response.json()}
    assert str(standard.id) in candidate_ids
    assert str(hidden.id) not in candidate_ids


async def test_hidden_agent_rejected_as_sub_agent(
    client: AsyncClient, db: AsyncSession
) -> None:
    standard, hidden = await _seed_agents(db)

    response = await client.put(
        f"/api/agents/{standard.id}",
        json={"sub_agent_ids": [str(hidden.id)]},
    )
    assert response.status_code == 400


async def test_hidden_agent_absent_from_assistant_subagent_listing(
    db: AsyncSession,
) -> None:
    import json
    from unittest.mock import patch

    from app.agent_runtime.assistant.tools.read_tools import build_read_tools
    from tests.conftest import TestSession

    standard, hidden = await _seed_agents(db)
    other = Agent(
        user_id=TEST_USER_ID,
        name="Other Standard",
        system_prompt="visible candidate",
        model_id=standard.model_id,
    )
    db.add(other)
    await db.commit()

    with patch(
        "app.agent_runtime.assistant.tools.read_tools.async_session_factory",
        TestSession,
    ):
        tools = build_read_tools(db, standard.id, TEST_USER_ID)
        tool = next(t for t in tools if t.name == "list_available_subagents")
        items = json.loads(await tool.ainvoke({}))

    ids = {item["id"] for item in items}
    assert str(other.id) in ids
    assert str(hidden.id) not in ids
