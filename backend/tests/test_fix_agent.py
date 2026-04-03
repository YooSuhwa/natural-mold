from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.fix_agent import run_fix_conversation
from app.models.agent import Agent
from app.models.model import Model as ModelDB
from app.models.tool import AgentToolLink, Tool
from tests.conftest import TEST_USER_ID

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_model(db: AsyncSession, **overrides) -> ModelDB:
    model = ModelDB(
        provider=overrides.get("provider", "openai"),
        model_name=overrides.get("model_name", "gpt-4o"),
        display_name=overrides.get("display_name", "GPT-4o"),
        is_default=overrides.get("is_default", True),
    )
    db.add(model)
    await db.commit()
    await db.refresh(model)
    return model


async def _seed_tool(db: AsyncSession, **overrides) -> Tool:
    tool = Tool(
        name=overrides.get("name", "Web Search"),
        description=overrides.get("description", "검색"),
        type=overrides.get("type", "prebuilt"),
        is_system=overrides.get("is_system", True),
        user_id=overrides.get("user_id", TEST_USER_ID),
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    return tool


async def _seed_agent(
    db: AsyncSession,
    model: ModelDB,
    tools: list[Tool] | None = None,
    **overrides,
) -> Agent:
    agent = Agent(
        user_id=overrides.get("user_id", TEST_USER_ID),
        name=overrides.get("name", "테스트 에이전트"),
        description=overrides.get("description", "테스트 설명"),
        system_prompt=overrides.get("system_prompt", "You are helpful."),
        model_id=model.id,
        model_params=overrides.get("model_params"),
    )
    if tools:
        agent.tool_links = [AgentToolLink(tool_id=t.id) for t in tools]
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent


async def _create_model_via_api(client: AsyncClient) -> str:
    resp = await client.post(
        "/api/models",
        json={
            "provider": "openai",
            "model_name": "gpt-4o",
            "display_name": "GPT-4o",
            "is_default": True,
        },
    )
    return resp.json()["id"]


async def _create_agent_via_api(client: AsyncClient, model_id: str) -> dict:
    resp = await client.post(
        "/api/agents",
        json={
            "name": "Fix 테스트 에이전트",
            "description": "Fix 대상",
            "system_prompt": "You are helpful.",
            "model_id": model_id,
        },
    )
    assert resp.status_code == 201
    return resp.json()


# ===========================================================================
# run_fix_conversation 단위 테스트 (LLM mock)
# ===========================================================================


class TestRunFixConversation:
    """fix_agent.run_fix_conversation 함수 테스트."""

    @pytest.mark.asyncio
    @patch("app.agent_runtime.fix_agent.create_chat_model")
    async def test_preview_action(self, mock_create):
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = AsyncMock(
            content="변경 사항을 미리 볼게요.\n```json\n"
            '{"action": "preview", "changes": {"system_prompt": "새 프롬프트"}, '
            '"summary": "프롬프트 변경 제안"}\n```'
        )
        mock_create.return_value = mock_model

        result = await run_fix_conversation(
            agent_info={
                "name": "에이전트",
                "description": "desc",
                "system_prompt": "원래 프롬프트",
                "model_name": "GPT-4o",
                "tool_names": ["Web Search"],
                "temperature": 0.7,
                "top_p": 1.0,
                "max_tokens": 4096,
            },
            conversation_history=[],
            user_message="프롬프트 바꿔줘",
        )

        assert result["action"] == "preview"
        assert result["changes"]["system_prompt"] == "새 프롬프트"
        assert result["summary"] == "프롬프트 변경 제안"
        assert result["role"] == "assistant"

    @pytest.mark.asyncio
    @patch("app.agent_runtime.fix_agent.create_chat_model")
    async def test_apply_action(self, mock_create):
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = AsyncMock(
            content="적용합니다.\n```json\n"
            '{"action": "apply", "changes": {"name": "새 이름"}, '
            '"summary": "이름 변경 완료"}\n```'
        )
        mock_create.return_value = mock_model

        result = await run_fix_conversation(
            agent_info={
                "name": "에이전트",
                "description": "",
                "system_prompt": "prompt",
                "model_name": "GPT-4o",
                "tool_names": [],
                "temperature": 0.7,
                "top_p": 1.0,
                "max_tokens": 4096,
            },
            conversation_history=[],
            user_message="적용해줘",
        )

        assert result["action"] == "apply"
        assert result["changes"]["name"] == "새 이름"

    @pytest.mark.asyncio
    @patch("app.agent_runtime.fix_agent.create_chat_model")
    async def test_ask_action(self, mock_create):
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = AsyncMock(
            content="좀 더 알려주세요.\n```json\n"
            '{"action": "ask", "question": "어떤 말투를 원하시나요?"}\n```'
        )
        mock_create.return_value = mock_model

        result = await run_fix_conversation(
            agent_info={
                "name": "에이전트",
                "description": "",
                "system_prompt": "",
                "model_name": "GPT-4o",
                "tool_names": [],
                "temperature": 0.7,
                "top_p": 1.0,
                "max_tokens": 4096,
            },
            conversation_history=[],
            user_message="말투 바꿔줘",
        )

        assert result["action"] == "ask"
        assert result["question"] == "어떤 말투를 원하시나요?"
        assert result["changes"] is None

    @pytest.mark.asyncio
    @patch("app.agent_runtime.fix_agent.create_chat_model")
    async def test_no_json_defaults_to_ask(self, mock_create):
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = AsyncMock(content="JSON 없는 일반 응답입니다.")
        mock_create.return_value = mock_model

        result = await run_fix_conversation(
            agent_info={
                "name": "에이전트",
                "description": "",
                "system_prompt": "",
                "model_name": "GPT-4o",
                "tool_names": [],
                "temperature": 0.7,
                "top_p": 1.0,
                "max_tokens": 4096,
            },
            conversation_history=[],
            user_message="안녕",
        )

        assert result["action"] == "ask"
        assert result["changes"] is None

    @pytest.mark.asyncio
    @patch("app.agent_runtime.fix_agent.create_chat_model")
    async def test_conversation_history_passed(self, mock_create):
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = AsyncMock(
            content='네.\n```json\n{"action": "ask", "question": "?"}\n```'
        )
        mock_create.return_value = mock_model

        history = [
            {"role": "user", "content": "이전 메시지"},
            {"role": "assistant", "content": "이전 응답"},
        ]

        await run_fix_conversation(
            agent_info={
                "name": "에이전트",
                "description": "",
                "system_prompt": "",
                "model_name": "GPT-4o",
                "tool_names": [],
                "temperature": 0.7,
                "top_p": 1.0,
                "max_tokens": 4096,
            },
            conversation_history=history,
            user_message="새 메시지",
        )

        call_args = mock_model.ainvoke.call_args[0][0]
        # system + 2 history + 1 user = 4 messages
        assert len(call_args) == 4

    @pytest.mark.asyncio
    @patch("app.agent_runtime.fix_agent.create_chat_model")
    async def test_available_tools_in_prompt(self, mock_create):
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = AsyncMock(
            content='```json\n{"action": "ask", "question": "?"}\n```'
        )
        mock_create.return_value = mock_model

        await run_fix_conversation(
            agent_info={
                "name": "에이전트",
                "description": "",
                "system_prompt": "",
                "model_name": "GPT-4o",
                "tool_names": [],
                "temperature": 0.7,
                "top_p": 1.0,
                "max_tokens": 4096,
            },
            conversation_history=[],
            user_message="도구 추가",
            available_tools=["Web Search", "Gmail"],
            available_models=["GPT-4o", "Claude 3.5"],
        )

        call_args = mock_model.ainvoke.call_args[0][0]
        system_content = call_args[0].content
        assert "Web Search, Gmail" in system_content
        assert "GPT-4o, Claude 3.5" in system_content

    @pytest.mark.asyncio
    @patch("app.agent_runtime.fix_agent.create_chat_model")
    async def test_no_available_tools_shows_none(self, mock_create):
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = AsyncMock(
            content='```json\n{"action": "ask", "question": "?"}\n```'
        )
        mock_create.return_value = mock_model

        await run_fix_conversation(
            agent_info={
                "name": "에이전트",
                "description": "",
                "system_prompt": "",
                "model_name": "GPT-4o",
                "tool_names": [],
                "temperature": 0.7,
                "top_p": 1.0,
                "max_tokens": 4096,
            },
            conversation_history=[],
            user_message="도구 추가",
            available_tools=None,
            available_models=None,
        )

        call_args = mock_model.ainvoke.call_args[0][0]
        system_content = call_args[0].content
        assert "없음" in system_content

    @pytest.mark.asyncio
    @patch("app.agent_runtime.fix_agent.create_chat_model")
    async def test_raw_content_preserved(self, mock_create):
        raw = '설명\n```json\n{"action": "preview", "changes": {}, "summary": "s"}\n```'
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = AsyncMock(content=raw)
        mock_create.return_value = mock_model

        result = await run_fix_conversation(
            agent_info={
                "name": "a",
                "description": "",
                "system_prompt": "",
                "model_name": "m",
                "tool_names": [],
                "temperature": 0.7,
                "top_p": 1.0,
                "max_tokens": 4096,
            },
            conversation_history=[],
            user_message="hi",
        )

        assert result["raw_content"] == raw
        # clean content should not contain json block
        assert "```json" not in result["content"]


# ===========================================================================
# Fix Agent Router 테스트 (API 레벨, LLM mock)
# ===========================================================================


class TestFixAgentRouter:
    """POST /api/agents/{agent_id}/fix 엔드포인트 테스트."""

    @pytest.mark.asyncio
    @patch("app.routers.fix_agent.run_fix_conversation")
    async def test_fix_agent_preview(self, mock_run, client: AsyncClient):
        mock_run.return_value = {
            "role": "assistant",
            "content": "프롬프트를 변경해볼게요.",
            "raw_content": "프롬프트를 변경해볼게요.\n```json...```",
            "action": "preview",
            "changes": {"system_prompt": "새 프롬프트"},
            "summary": "프롬프트 변경 제안",
            "question": None,
        }

        model_id = await _create_model_via_api(client)
        agent = await _create_agent_via_api(client, model_id)
        agent_id = agent["id"]

        resp = await client.post(
            f"/api/agents/{agent_id}/fix",
            json={"content": "프롬프트 바꿔줘"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "preview"
        assert data["changes"]["system_prompt"] == "새 프롬프트"
        assert len(data["conversation_history"]) == 2  # user + assistant

    @pytest.mark.asyncio
    @patch("app.routers.fix_agent.run_fix_conversation")
    async def test_fix_agent_apply_updates_agent(self, mock_run, client: AsyncClient):
        mock_run.return_value = {
            "role": "assistant",
            "content": "적용했습니다.",
            "raw_content": "적용했습니다.",
            "action": "apply",
            "changes": {"name": "변경된 에이전트"},
            "summary": "이름 변경 완료",
            "question": None,
        }

        model_id = await _create_model_via_api(client)
        agent = await _create_agent_via_api(client, model_id)
        agent_id = agent["id"]

        resp = await client.post(
            f"/api/agents/{agent_id}/fix",
            json={"content": "적용해줘"},
        )
        assert resp.status_code == 200
        assert resp.json()["action"] == "apply"

        # 실제로 에이전트가 업데이트되었는지 확인
        resp = await client.get(f"/api/agents/{agent_id}")
        assert resp.json()["name"] == "변경된 에이전트"

    @pytest.mark.asyncio
    @patch("app.routers.fix_agent.run_fix_conversation")
    async def test_fix_agent_apply_system_prompt(self, mock_run, client: AsyncClient):
        mock_run.return_value = {
            "role": "assistant",
            "content": "적용.",
            "raw_content": "적용.",
            "action": "apply",
            "changes": {"system_prompt": "새 시스템 프롬프트"},
            "summary": "프롬프트 변경",
            "question": None,
        }

        model_id = await _create_model_via_api(client)
        agent = await _create_agent_via_api(client, model_id)
        agent_id = agent["id"]

        resp = await client.post(
            f"/api/agents/{agent_id}/fix",
            json={"content": "적용"},
        )
        assert resp.status_code == 200

        resp = await client.get(f"/api/agents/{agent_id}")
        assert resp.json()["system_prompt"] == "새 시스템 프롬프트"

    @pytest.mark.asyncio
    @patch("app.routers.fix_agent.run_fix_conversation")
    async def test_fix_agent_apply_description(self, mock_run, client: AsyncClient):
        mock_run.return_value = {
            "role": "assistant",
            "content": "적용.",
            "raw_content": "적용.",
            "action": "apply",
            "changes": {"description": "새 설명"},
            "summary": "설명 변경",
            "question": None,
        }

        model_id = await _create_model_via_api(client)
        agent = await _create_agent_via_api(client, model_id)
        agent_id = agent["id"]

        resp = await client.post(
            f"/api/agents/{agent_id}/fix",
            json={"content": "적용"},
        )
        assert resp.status_code == 200

        resp = await client.get(f"/api/agents/{agent_id}")
        assert resp.json()["description"] == "새 설명"

    @pytest.mark.asyncio
    @patch("app.routers.fix_agent.run_fix_conversation")
    async def test_fix_agent_apply_model_params(self, mock_run, client: AsyncClient):
        mock_run.return_value = {
            "role": "assistant",
            "content": "적용.",
            "raw_content": "적용.",
            "action": "apply",
            "changes": {"model_params": {"temperature": 0.2, "top_p": 0.5}},
            "summary": "파라미터 변경",
            "question": None,
        }

        model_id = await _create_model_via_api(client)
        agent = await _create_agent_via_api(client, model_id)
        agent_id = agent["id"]

        resp = await client.post(
            f"/api/agents/{agent_id}/fix",
            json={"content": "적용"},
        )
        assert resp.status_code == 200

        resp = await client.get(f"/api/agents/{agent_id}")
        assert resp.json()["model_params"]["temperature"] == 0.2

    @pytest.mark.asyncio
    @patch("app.routers.fix_agent.run_fix_conversation")
    async def test_fix_agent_ask(self, mock_run, client: AsyncClient):
        mock_run.return_value = {
            "role": "assistant",
            "content": "좀 더 알려주세요.",
            "raw_content": "좀 더 알려주세요.",
            "action": "ask",
            "changes": None,
            "summary": None,
            "question": "어떤 말투를 원하시나요?",
        }

        model_id = await _create_model_via_api(client)
        agent = await _create_agent_via_api(client, model_id)
        agent_id = agent["id"]

        resp = await client.post(
            f"/api/agents/{agent_id}/fix",
            json={"content": "말투 바꿔"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "ask"
        assert data["question"] == "어떤 말투를 원하시나요?"
        assert data["changes"] is None

    @pytest.mark.asyncio
    async def test_fix_agent_not_found(self, client: AsyncClient):
        fake_id = "00000000-0000-0000-0000-000000000099"
        resp = await client.post(
            f"/api/agents/{fake_id}/fix",
            json={"content": "수정해줘"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @patch("app.routers.fix_agent.run_fix_conversation")
    async def test_fix_agent_with_conversation_history(self, mock_run, client: AsyncClient):
        mock_run.return_value = {
            "role": "assistant",
            "content": "네.",
            "raw_content": "네.",
            "action": "ask",
            "changes": None,
            "summary": None,
            "question": "다음 질문",
        }

        model_id = await _create_model_via_api(client)
        agent = await _create_agent_via_api(client, model_id)
        agent_id = agent["id"]

        history = [
            {"role": "user", "content": "이전 메시지"},
            {"role": "assistant", "content": "이전 응답"},
        ]

        resp = await client.post(
            f"/api/agents/{agent_id}/fix",
            json={"content": "계속", "conversation_history": history},
        )
        assert resp.status_code == 200
        data = resp.json()
        # 기존 2 + user 1 + assistant 1 = 4
        assert len(data["conversation_history"]) == 4

    @pytest.mark.asyncio
    @patch("app.routers.fix_agent.run_fix_conversation")
    async def test_fix_agent_apply_add_tools(self, mock_run, client: AsyncClient, db: AsyncSession):
        """도구 추가 적용 시 에이전트에 도구가 연결되는지 확인."""
        await _seed_tool(db, name="Web Search", is_system=True)

        mock_run.return_value = {
            "role": "assistant",
            "content": "도구를 추가했습니다.",
            "raw_content": "도구를 추가했습니다.",
            "action": "apply",
            "changes": {"add_tools": ["Web Search"]},
            "summary": "Web Search 추가",
            "question": None,
        }

        model_id = await _create_model_via_api(client)
        agent = await _create_agent_via_api(client, model_id)
        agent_id = agent["id"]

        resp = await client.post(
            f"/api/agents/{agent_id}/fix",
            json={"content": "검색 도구 추가해줘"},
        )
        assert resp.status_code == 200

        # 에이전트에 도구가 연결되었는지 확인
        resp = await client.get(f"/api/agents/{agent_id}")
        tools = resp.json()["tools"]
        tool_names = [t["name"] for t in tools]
        assert "Web Search" in tool_names

    @pytest.mark.asyncio
    @patch("app.routers.fix_agent.run_fix_conversation")
    async def test_fix_agent_apply_remove_tools(
        self,
        mock_run,
        client: AsyncClient,
        db: AsyncSession,
    ):
        """도구 제거 적용 테스트."""
        seeded_tool = await _seed_tool(db, name="Web Search", is_system=True)

        # 에이전트 생성 + 도구 연결
        model_id = await _create_model_via_api(client)
        resp = await client.post(
            "/api/agents",
            json={
                "name": "도구 테스트",
                "description": "desc",
                "system_prompt": "prompt",
                "model_id": model_id,
                "tool_ids": [str(seeded_tool.id)],
            },
        )
        agent_id = resp.json()["id"]

        mock_run.return_value = {
            "role": "assistant",
            "content": "도구를 제거했습니다.",
            "raw_content": "도구를 제거했습니다.",
            "action": "apply",
            "changes": {"remove_tools": ["Web Search"]},
            "summary": "Web Search 제거",
            "question": None,
        }

        resp = await client.post(
            f"/api/agents/{agent_id}/fix",
            json={"content": "검색 도구 빼줘"},
        )
        assert resp.status_code == 200

        resp = await client.get(f"/api/agents/{agent_id}")
        assert len(resp.json()["tools"]) == 0

    @pytest.mark.asyncio
    @patch("app.routers.fix_agent.run_fix_conversation")
    async def test_fix_agent_apply_model_change(self, mock_run, client: AsyncClient):
        """모델 변경 적용 테스트."""
        model_id = await _create_model_via_api(client)

        # 두 번째 모델 생성
        resp = await client.post(
            "/api/models",
            json={
                "provider": "anthropic",
                "model_name": "claude-3-5-sonnet",
                "display_name": "Claude 3.5 Sonnet",
                "is_default": False,
            },
        )
        resp.json()["id"]

        agent = await _create_agent_via_api(client, model_id)
        agent_id = agent["id"]

        mock_run.return_value = {
            "role": "assistant",
            "content": "모델을 변경했습니다.",
            "raw_content": "모델을 변경했습니다.",
            "action": "apply",
            "changes": {"model_name": "Claude 3.5 Sonnet"},
            "summary": "모델 변경 완료",
            "question": None,
        }

        resp = await client.post(
            f"/api/agents/{agent_id}/fix",
            json={"content": "Claude로 바꿔줘"},
        )
        assert resp.status_code == 200

        resp = await client.get(f"/api/agents/{agent_id}")
        assert resp.json()["model"]["display_name"] == "Claude 3.5 Sonnet"

    @pytest.mark.asyncio
    @patch("app.routers.fix_agent.run_fix_conversation")
    async def test_fix_agent_preview_does_not_apply(self, mock_run, client: AsyncClient):
        """preview 액션은 에이전트를 수정하지 않아야 함."""
        mock_run.return_value = {
            "role": "assistant",
            "content": "미리보기입니다.",
            "raw_content": "미리보기입니다.",
            "action": "preview",
            "changes": {"name": "바뀌면 안 됨"},
            "summary": "미리보기",
            "question": None,
        }

        model_id = await _create_model_via_api(client)
        agent = await _create_agent_via_api(client, model_id)
        agent_id = agent["id"]

        await client.post(
            f"/api/agents/{agent_id}/fix",
            json={"content": "이름 바꿔줘"},
        )

        resp = await client.get(f"/api/agents/{agent_id}")
        assert resp.json()["name"] == "Fix 테스트 에이전트"  # 원래 이름 유지
