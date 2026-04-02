from __future__ import annotations

import uuid
from decimal import Decimal

from app.schemas.token_usage import AgentUsageRow, TokenUsageResponse, UsageSummaryResponse


class TestTokenUsageResponse:
    def test_creation(self):
        agent_id = uuid.uuid4()
        resp = TokenUsageResponse(
            agent_id=agent_id,
            period="2026-04",
            total_tokens=1000,
            prompt_tokens=700,
            completion_tokens=300,
            estimated_cost_usd=Decimal("0.025"),
        )
        assert resp.agent_id == agent_id
        assert resp.period == "2026-04"
        assert resp.total_tokens == 1000
        assert resp.prompt_tokens == 700
        assert resp.completion_tokens == 300
        assert resp.estimated_cost_usd == Decimal("0.025")

    def test_decimal_precision(self):
        resp = TokenUsageResponse(
            agent_id=uuid.uuid4(),
            period="2026-04",
            total_tokens=500,
            prompt_tokens=300,
            completion_tokens=200,
            estimated_cost_usd=Decimal("0.00123456"),
        )
        assert resp.estimated_cost_usd == Decimal("0.00123456")


class TestAgentUsageRow:
    def test_creation(self):
        agent_id = uuid.uuid4()
        row = AgentUsageRow(
            agent_id=agent_id,
            agent_name="My Agent",
            total_tokens=5000,
            estimated_cost=Decimal("0.15"),
        )
        assert row.agent_id == agent_id
        assert row.agent_name == "My Agent"
        assert row.total_tokens == 5000
        assert row.estimated_cost == Decimal("0.15")


class TestUsageSummaryResponse:
    def test_creation_with_by_agent(self):
        agent_id1 = uuid.uuid4()
        agent_id2 = uuid.uuid4()
        summary = UsageSummaryResponse(
            period="2026-04",
            total_tokens=10000,
            prompt_tokens=7000,
            completion_tokens=3000,
            estimated_cost_usd=Decimal("0.30"),
            by_agent=[
                AgentUsageRow(
                    agent_id=agent_id1,
                    agent_name="Agent A",
                    total_tokens=6000,
                    estimated_cost=Decimal("0.18"),
                ),
                AgentUsageRow(
                    agent_id=agent_id2,
                    agent_name="Agent B",
                    total_tokens=4000,
                    estimated_cost=Decimal("0.12"),
                ),
            ],
        )
        assert summary.period == "2026-04"
        assert summary.total_tokens == 10000
        assert len(summary.by_agent) == 2
        assert summary.by_agent[0].agent_name == "Agent A"
        assert summary.by_agent[1].agent_name == "Agent B"

    def test_empty_by_agent(self):
        summary = UsageSummaryResponse(
            period="2026-04",
            total_tokens=0,
            prompt_tokens=0,
            completion_tokens=0,
            estimated_cost_usd=Decimal("0"),
            by_agent=[],
        )
        assert summary.by_agent == []
        assert summary.estimated_cost_usd == Decimal("0")
