"""Both chat compilers send an informational attachment note only at the model boundary."""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import checkpointer, runtime_component_builder
from app.agent_runtime.omitted_media import OMITTED_MEDIA_NOTE
from app.agent_runtime.prepared_run_dispatch import handle_for, invoke_prepared, lease_run_config
from app.agent_runtime.runtime_config import RuntimeComponents
from app.schemas.conversation_graph_input import OMITTED_MEDIA_KEY
from tests.authz import prepared_adapter_testkit as akit
from tests.authz import prepared_materialization_testkit as kit
from tests.test_executor import _cfg


def _turn() -> dict[str, Any]:
    return {
        "messages": [
            {
                "role": "user",
                "id": str(uuid4()),
                "content": "",
                "additional_kwargs": {OMITTED_MEDIA_KEY: True},
            }
        ]
    }


def _assert_model_and_history(seen: list[BaseMessage], history: list[BaseMessage]) -> None:
    (model_user,) = (m for m in seen if isinstance(m, HumanMessage))
    (saved_user,) = (m for m in history if isinstance(m, HumanMessage))
    assert model_user.id == saved_user.id
    assert model_user.content == OMITTED_MEDIA_NOTE and model_user.additional_kwargs == {}
    assert saved_user.content == "" and saved_user.additional_kwargs == {OMITTED_MEDIA_KEY: True}


async def test_ordinary_graph_retains_empty_user_content(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[BaseMessage] = []

    class CaptureModel(FakeMessagesListChatModel):
        def bind_tools(self, tools: Any, **kwargs: Any) -> "CaptureModel":
            return self

        def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
            seen.extend(messages)
            return super()._generate(messages, *args, **kwargs)

    model = CaptureModel(responses=[AIMessage(content="ok")])
    components = RuntimeComponents(
        model_candidates=[model],
        model=model,
        tools=[],
        middleware=[],
        system_prompt="test",
        skills_sources=None,
        backend=None,
        memory_sources=None,
        permissions=[],
        interrupt_on=None,
    )
    monkeypatch.setattr(checkpointer, "get_checkpointer", InMemorySaver)
    monkeypatch.setattr(
        runtime_component_builder, "_prepare_runtime_components", AsyncMock(return_value=components)
    )
    graph, _, config = await runtime_component_builder._prepare_agent(
        _cfg(thread_id=str(uuid4())), messages_history=[]
    )
    await graph.ainvoke(_turn(), config)
    checkpoint = await graph.aget_state(config)
    _assert_model_and_history(seen, checkpoint.values["messages"])


async def test_prepared_lease_retains_empty_user_content(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[BaseMessage] = []
    generate = kit.GuardedScriptedModel._agenerate

    async def record(model: Any, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
        seen.extend(messages)
        return await generate(model, messages, *args, **kwargs)

    monkeypatch.setattr(kit.GuardedScriptedModel, "_agenerate", record)
    active = await akit.activate(
        db,
        tmp_path,
        monkeypatch,
        lambda seed: {seed.root_model: [AIMessage(content="ok")], seed.child_model: []},
    )
    graph, config = active.lease.runnable, lease_run_config(active.lease)
    await invoke_prepared(handle_for(active.lease), stream_input=_turn())
    checkpoint = await graph.aget_state(config)
    _assert_model_and_history(seen, checkpoint.values["messages"])
