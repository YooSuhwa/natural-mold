from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from pathlib import Path

import pytest
from httpx import AsyncClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder.deep_agent_worker import SandboxedDeepAgentDraftWorker
from app.agent_runtime.skill_builder.graph import JsonChatDraftWorker, run_skill_builder_graph
from app.credentials import service as credential_service
from app.models.system_llm_setting import SystemLlmSetting

pytestmark = pytest.mark.asyncio

BASE = "/api/skill-builder"


async def _configure_system_llm(db: AsyncSession) -> None:
    credential = await credential_service.create(
        db,
        user_id=None,
        definition_key="openai",
        name="builder-key",
        data={"api_key": "sk-test"},
        is_system=True,
    )
    db.add(
        SystemLlmSetting(
            role="text_primary",
            credential_id=credential.id,
            model_name="gpt-5.4",
        )
    )
    await db.commit()


def _events(text: str) -> list[tuple[str, dict[str, object]]]:
    result: list[tuple[str, dict[str, object]]] = []
    for block in text.strip().split("\n\n"):
        name = ""
        data = {}
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            if line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
        if name:
            result.append((name, data))
    return result


def _event_names(events: Sequence[tuple[str, dict[str, object]]]) -> list[str]:
    return [name for name, _data in events]


async def test_hidden_graph_with_fake_chat_model_produces_draft_validation_and_changelog() -> None:
    fake_model = FakeListChatModel(
        responses=[
            json.dumps(
                {
                    "name": "Fake Notes",
                    "slug": "fake-notes",
                    "description": "Use when testing the hidden graph.",
                    "files": [
                        {
                            "path": "SKILL.md",
                            "content": (
                                "---\n"
                                "name: fake-notes\n"
                                'description: "Use when testing the hidden graph."\n'
                                "---\n\n"
                                "Intent: fake model draft\n"
                            ),
                            "role": "skill",
                        }
                    ],
                    "credential_requirements": [],
                    "execution_profile": {"requires_network": False},
                }
            )
        ]
    )
    result = await run_skill_builder_graph(
        state={
            "user_id": "00000000-0000-0000-0000-000000000001",
            "session_id": "00000000-0000-0000-0000-000000000002",
            "mode": "create",
            "source_skill_id": None,
            "base_snapshot": None,
            "user_request": "회의록 액션 아이템 스킬",
            "current_phase": 0,
        },
        draft_worker=JsonChatDraftWorker(fake_model),
    )

    assert result["intent"]["summary"] == "회의록 액션 아이템 스킬"
    assert result["draft_package"]["slug"] == "fake-notes"
    assert result["validation_result"]["error_count"] == 0
    assert result["compatibility_result"]["targets"]
    assert result["changelog_draft"]["summary"] == "Created skill package with 1 file."
    assert result["next_action"] == "review"


async def test_sandboxed_deep_agent_draft_worker_uses_session_scoped_storage(
    tmp_path: Path,
) -> None:
    session_id = uuid.uuid4()
    worker = SandboxedDeepAgentDraftWorker(draft_root=tmp_path / "drafts")

    draft = await worker.draft(
        {
            "user_id": str(uuid.uuid4()),
            "session_id": str(session_id),
            "mode": "create",
            "source_skill_id": None,
            "base_snapshot": None,
            "user_request": "회의록 액션 아이템 스킬",
            "current_phase": 0,
        }
    )

    assert worker.session_root(session_id) == (tmp_path / "drafts" / str(session_id)).resolve()
    assert worker.session_root(session_id).is_dir()
    assert draft.slug
    assert not (tmp_path / "skills").exists()


async def test_message_stream_emits_builder_events_and_persists_session(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _configure_system_llm(db)
    start = await client.post(
        BASE,
        json={"mode": "create", "user_request": "회의록 액션 아이템 스킬 만들어줘"},
    )
    session_id = start.json()["id"]

    response = await client.post(
        f"{BASE}/{session_id}/messages",
        json={"content": "담당자와 마감일을 표로 뽑는 스킬로 만들어줘"},
    )
    events = _events(response.text)
    session = await client.get(f"{BASE}/{session_id}")

    assert response.status_code == 200, response.text
    assert _event_names(events) == [
        "message_start",
        "builder_status",
        "builder_status",
        "draft_package",
        "builder_activity",
        "validation_result",
        "compatibility_result",
        "builder_activity",
        "changelog_draft",
        "eval_result",
        "content_delta",
        "builder_status",
        "message_end",
    ]
    draft_event = dict(events)["draft_package"]
    assert draft_event["file_count"] == 2
    assert draft_event["files"] == [
        {"path": "SKILL.md", "role": "skill"},
        {"path": "agents/openai.yaml", "role": "metadata"},
    ]
    assert "content" not in json.dumps(draft_event)

    body = session.json()
    assert body["status"] == "review"
    assert body["draft_package"]["slug"]
    assert body["validation_result"]["error_count"] == 0
    assert body["compatibility_result"]["targets"]
    assert body["changelog_draft"]["summary"]
    assert [message["role"] for message in body["messages"]] == ["user", "assistant"]
