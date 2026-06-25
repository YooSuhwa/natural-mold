from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.e2e_langgraph_v3_script import (
    LANGGRAPH_V3_SECRET_TOOL_ARG_VALUE,
    LANGGRAPH_V3_SECRET_TOOL_ARGS_REQUEST,
)
from app.agent_runtime.e2e_scripted_model import (
    ASK_USER_FRUIT_FINAL_CONTENT,
    ASK_USER_FRUIT_MARKER,
    ASK_USER_FRUIT_PREFACE_CONTENT,
    ASK_USER_FRUIT_TOOL_ARGS,
    ASK_USER_FRUIT_TOOL_CALL_ID,
    CHAT_RICH_OUTPUT_CONTENT,
    CHAT_RICH_OUTPUT_PROMPT,
    HITL_APPROVAL_MARKER,
    HITL_MULTI_MARKER,
    HITL_MULTI_TOOL_CALLS,
    SCRIPTED_DOCUMENT_COMMANDS,
    E2EScriptedChatModel,
)
from app.models.model import Model
from app.seed.e2e_scripted_model import (
    E2E_SCRIPTED_MODEL_NAME,
    E2E_SCRIPTED_PROVIDER,
    seed_e2e_scripted_model,
)


def test_e2e_scripted_model_emits_execute_in_skill_tool_call() -> None:
    model = E2EScriptedChatModel(model="document-artifact-scripted")

    result = model.invoke([HumanMessage(content="E2E_DOCX 문서를 생성해줘")])

    assert result.tool_calls == [
        {
            "name": "execute_in_skill",
            "args": {
                "skill_directory": "/skills/docx-document",
                "command": (
                    "node scripts/create_docx.cjs --input examples/e2e-docx.json "
                    "--output moldy-docx-demo.docx"
                ),
            },
            "id": "call_e2e_docx",
            "type": "tool_call",
        }
    ]


def test_e2e_scripted_model_uses_latest_human_message_marker() -> None:
    model = E2EScriptedChatModel(model="document-artifact-scripted")

    result = model.invoke(
        [
            HumanMessage(content="E2E_DOCX 문서를 생성해줘"),
            HumanMessage(content="이제 E2E_XLSX 문서를 생성해줘"),
        ]
    )

    assert result.tool_calls[0]["id"] == "call_e2e_xlsx"
    assert result.tool_calls[0]["args"] == {
        "skill_directory": "/skills/xlsx-spreadsheet",
        "command": (
            "node scripts/create_xlsx.cjs --input examples/e2e-xlsx.json "
            "--output moldy-xlsx-demo.xlsx"
        ),
    }


def test_e2e_scripted_model_returns_final_message_after_tool_result() -> None:
    model = E2EScriptedChatModel(model="document-artifact-scripted")

    result = model.invoke(
        [
            HumanMessage(content="E2E_DOCX"),
            ToolMessage(content="OUTPUT_FILES: moldy-docx-demo.docx", tool_call_id="call_e2e_docx"),
        ]
    )

    assert "문서 파일 생성이 완료" in str(result.content)


def test_e2e_scripted_model_streams_slow_marker_in_chunks() -> None:
    model = E2EScriptedChatModel(
        model="document-artifact-scripted",
        slow_stream_delay_seconds=0,
    )

    chunks = list(model.stream([HumanMessage(content="E2E_SLOW_STREAM")]))
    content = "".join(str(chunk.content) for chunk in chunks)

    assert len([chunk for chunk in chunks if chunk.content]) >= 3
    assert "E2E slow stream completed" in content


def test_e2e_scripted_model_streams_visual_slow_marker_in_chunks() -> None:
    model = E2EScriptedChatModel(
        model="document-artifact-scripted",
        slow_stream_delay_seconds=0,
    )

    chunks = list(model.stream([HumanMessage(content="E2E_VISUAL_SLOW_STREAM")]))
    content = "".join(str(chunk.content) for chunk in chunks)

    assert len([chunk for chunk in chunks if chunk.content]) >= 20
    assert "E2E visual stream fixture is still running" in content
    assert content.endswith("fixture complete.")


def test_e2e_scripted_model_streams_slow_final_after_tool_result_marker() -> None:
    model = E2EScriptedChatModel(
        model="document-artifact-scripted",
        slow_stream_delay_seconds=0,
    )

    chunks = list(
        model.stream(
            [
                HumanMessage(content="E2E_DOCX E2E_ARTIFACT_SLOW_FINAL"),
                ToolMessage(
                    content="OUTPUT_FILES: moldy-docx-demo.docx",
                    tool_call_id="call_e2e_docx",
                ),
            ]
        )
    )
    content = "".join(str(chunk.content) for chunk in chunks)

    assert len([chunk for chunk in chunks if chunk.content]) >= 3
    assert "E2E artifact final response is still streaming" in content
    assert "completed after generated file" in content


def test_e2e_scripted_model_stream_preserves_document_tool_call() -> None:
    model = E2EScriptedChatModel(
        model="document-artifact-scripted",
        slow_stream_delay_seconds=0,
    )

    chunks = list(model.stream([HumanMessage(content="E2E_DOCX 문서를 생성해줘")]))

    tool_calls = [tool_call for chunk in chunks for tool_call in chunk.tool_calls]
    assert tool_calls == [
        {
            "name": "execute_in_skill",
            "args": {
                "skill_directory": "/skills/docx-document",
                "command": (
                    "node scripts/create_docx.cjs --input examples/e2e-docx.json "
                    "--output moldy-docx-demo.docx"
                ),
            },
            "id": "call_e2e_docx",
            "type": "tool_call",
        }
    ]


def test_e2e_scripted_model_langgraph_v3_starts_with_todos() -> None:
    model = E2EScriptedChatModel(model="document-artifact-scripted").bind_tools(
        [{"name": "write_todos"}, {"name": "task"}, {"name": "execute_in_skill"}]
    )

    result = model.invoke([HumanMessage(content="E2E_LANGGRAPH_V3 subagent=agent_1234abcd")])

    assert result.tool_calls == [
        {
            "name": "write_todos",
            "args": {
                "todos": [
                    {
                        "content": "Collect LangGraph v3 runtime evidence",
                        "status": "completed",
                    },
                    {
                        "content": "Render delegated subagent progress",
                        "status": "in_progress",
                    },
                    {
                        "content": "Preview generated artifact and replay state",
                        "status": "pending",
                    },
                ]
            },
            "id": "call_e2e_langgraph_v3_todos",
            "type": "tool_call",
        }
    ]


def test_e2e_scripted_model_langgraph_v3_delegates_after_todos() -> None:
    model = E2EScriptedChatModel(model="document-artifact-scripted").bind_tools(
        [{"name": "write_todos"}, {"name": "task"}, {"name": "execute_in_skill"}]
    )

    result = model.invoke(
        [
            HumanMessage(content="E2E_LANGGRAPH_V3 subagent=agent_1234abcd"),
            ToolMessage(content="todos saved", tool_call_id="call_e2e_langgraph_v3_todos"),
        ]
    )

    assert result.tool_calls == [
        {
            "name": "task",
            "args": {
                "subagent_type": "agent_1234abcd",
                "description": "E2E_SUBAGENT summarize scoped LangGraph v3 work.",
            },
            "id": "call_e2e_langgraph_v3_subagent",
            "type": "tool_call",
        }
    ]


def test_e2e_scripted_model_langgraph_v3_can_request_slow_subagent() -> None:
    model = E2EScriptedChatModel(model="document-artifact-scripted").bind_tools(
        [{"name": "write_todos"}, {"name": "task"}, {"name": "execute_in_skill"}]
    )

    result = model.invoke(
        [
            HumanMessage(content="E2E_LANGGRAPH_V3 slow_subagent=true subagent=agent_1234abcd"),
            ToolMessage(content="todos saved", tool_call_id="call_e2e_langgraph_v3_todos"),
        ]
    )

    assert result.tool_calls == [
        {
            "name": "task",
            "args": {
                "subagent_type": "agent_1234abcd",
                "description": "E2E_SUBAGENT_SLOW summarize scoped LangGraph v3 work.",
            },
            "id": "call_e2e_langgraph_v3_subagent",
            "type": "tool_call",
        }
    ]


def test_e2e_scripted_model_langgraph_v3_generates_artifact_after_subagent() -> None:
    model = E2EScriptedChatModel(model="document-artifact-scripted").bind_tools(
        [{"name": "write_todos"}, {"name": "task"}, {"name": "execute_in_skill"}]
    )

    result = model.invoke(
        [
            HumanMessage(content="E2E_LANGGRAPH_V3 subagent=agent_1234abcd"),
            ToolMessage(content="todos saved", tool_call_id="call_e2e_langgraph_v3_todos"),
            ToolMessage(
                content="E2E subagent scoped result ready.",
                tool_call_id="call_e2e_langgraph_v3_subagent",
            ),
        ]
    )

    assert result.tool_calls == [
        {
            "name": "execute_in_skill",
            "args": {
                "skill_directory": "/skills/docx-document",
                "command": (
                    "node scripts/create_langgraph_v3_artifacts.cjs --prefix moldy-langgraph-v3"
                ),
            },
            "id": "call_e2e_langgraph_v3_docx",
            "type": "tool_call",
        }
    ]


def test_e2e_scripted_model_langgraph_v3_can_emit_sensitive_tool_arg_fixture() -> None:
    model = E2EScriptedChatModel(model="document-artifact-scripted").bind_tools(
        [{"name": "write_todos"}, {"name": "task"}, {"name": "execute_in_skill"}]
    )

    result = model.invoke(
        [
            HumanMessage(
                content=(
                    "E2E_LANGGRAPH_V3 "
                    f"{LANGGRAPH_V3_SECRET_TOOL_ARGS_REQUEST} "
                    "subagent=agent_1234abcd"
                )
            ),
            ToolMessage(content="todos saved", tool_call_id="call_e2e_langgraph_v3_todos"),
            ToolMessage(
                content="E2E subagent scoped result ready.",
                tool_call_id="call_e2e_langgraph_v3_subagent",
            ),
        ]
    )

    assert result.tool_calls[0]["args"]["api_key"] == LANGGRAPH_V3_SECRET_TOOL_ARG_VALUE


def test_e2e_scripted_model_langgraph_v3_returns_usage_after_artifact() -> None:
    model = E2EScriptedChatModel(model="document-artifact-scripted").bind_tools(
        [{"name": "write_todos"}, {"name": "task"}, {"name": "execute_in_skill"}]
    )
    messages = [
        HumanMessage(content="E2E_LANGGRAPH_V3 subagent=agent_1234abcd"),
        ToolMessage(content="todos saved", tool_call_id="call_e2e_langgraph_v3_todos"),
        ToolMessage(
            content="E2E subagent scoped result ready.",
            tool_call_id="call_e2e_langgraph_v3_subagent",
        ),
        ToolMessage(
            content="OUTPUT_FILES: moldy-langgraph-v3-report.md, moldy-langgraph-v3-notes.txt",
            tool_call_id="call_e2e_langgraph_v3_docx",
        ),
    ]

    result = model.invoke(messages)
    chunks = list(model.stream(messages))

    assert "E2E LangGraph v3 validation complete" in str(result.content)
    assert result.usage_metadata == {
        "input_tokens": 120,
        "output_tokens": 45,
        "total_tokens": 165,
    }
    assert any(chunk.usage_metadata == result.usage_metadata for chunk in chunks)


def test_e2e_scripted_model_emits_usage_stream_marker() -> None:
    model = E2EScriptedChatModel(
        model="document-artifact-scripted",
        slow_stream_delay_seconds=0,
    )

    chunks = list(model.stream([HumanMessage(content="E2E_TOKEN_USAGE_STREAM")]))
    content = "".join(str(chunk.content) for chunk in chunks)

    assert content == "E2E token usage isolated conversation response."
    assert any(
        chunk.usage_metadata == {"input_tokens": 30, "output_tokens": 12, "total_tokens": 42}
        for chunk in chunks
    )


def test_e2e_scripted_model_emits_rich_chat_output_fixture() -> None:
    model = E2EScriptedChatModel(model="document-artifact-scripted")

    result = model.invoke([HumanMessage(content=CHAT_RICH_OUTPUT_PROMPT)])

    assert result.content == CHAT_RICH_OUTPUT_CONTENT
    assert "| Table | E2E table cell |" in str(result.content)
    assert "```ts" in str(result.content)
    assert "$$" in str(result.content)
    assert "![E2E rich output image](/moldy-mascot.webp)" in str(result.content)


def test_e2e_scripted_model_emits_ask_user_tool_call() -> None:
    model = E2EScriptedChatModel(model="document-artifact-scripted").bind_tools(
        [{"name": "ask_user"}]
    )

    result = model.invoke([HumanMessage(content=f"{ASK_USER_FRUIT_MARKER} 과일을 골라줘")])

    assert result.content == ASK_USER_FRUIT_PREFACE_CONTENT
    assert result.tool_calls == [
        {
            "name": "ask_user",
            "args": ASK_USER_FRUIT_TOOL_ARGS,
            "id": ASK_USER_FRUIT_TOOL_CALL_ID,
            "type": "tool_call",
        }
    ]


def test_e2e_scripted_model_emits_ask_user_tool_call_for_natural_request() -> None:
    model = E2EScriptedChatModel(model="document-artifact-scripted").bind_tools(
        [{"name": "ask_user"}]
    )

    result = model.invoke([HumanMessage(content="사과, 포도, 배 중에 하나 선택하는 ask user 해줘")])

    assert result.content == ASK_USER_FRUIT_PREFACE_CONTENT
    assert result.tool_calls == [
        {
            "name": "ask_user",
            "args": ASK_USER_FRUIT_TOOL_ARGS,
            "id": ASK_USER_FRUIT_TOOL_CALL_ID,
            "type": "tool_call",
        }
    ]


def test_e2e_scripted_model_emits_hitl_approval_tool_call_for_natural_request() -> None:
    model = E2EScriptedChatModel(model="document-artifact-scripted").bind_tools(
        [{"name": "execute_in_skill"}]
    )

    result = model.invoke([HumanMessage(content="mcp 도구 사용 승인 HITL")])

    assert result.tool_calls == [
        {
            "name": "execute_in_skill",
            "args": SCRIPTED_DOCUMENT_COMMANDS["E2E_DOCX"],
            "id": "call_e2e_docx",
            "type": "tool_call",
        }
    ]


def test_e2e_scripted_model_emits_hitl_approval_tool_call_for_explicit_marker() -> None:
    model = E2EScriptedChatModel(model="document-artifact-scripted").bind_tools(
        [{"name": "execute_in_skill"}]
    )

    result = model.invoke([HumanMessage(content=f"{HITL_APPROVAL_MARKER} please")])

    assert result.tool_calls == [
        {
            "name": "execute_in_skill",
            "args": SCRIPTED_DOCUMENT_COMMANDS["E2E_DOCX"],
            "id": "call_e2e_docx",
            "type": "tool_call",
        }
    ]


def test_e2e_scripted_model_emits_two_execute_in_skill_tool_calls_for_hitl_multi() -> None:
    # langchain's HumanInTheLoopMiddleware batches every interrupting tool call from
    # a single AIMessage into ONE interrupt with N action_requests. Emitting two
    # execute_in_skill calls in one message exercises the multi-action approval-card
    # + HiTL coordinator path.
    model = E2EScriptedChatModel(model="document-artifact-scripted").bind_tools(
        [{"name": "execute_in_skill"}]
    )

    result = model.invoke([HumanMessage(content=f"{HITL_MULTI_MARKER} 멀티 승인 카드")])

    assert [(call["id"], call["name"]) for call in result.tool_calls] == [
        ("call_e2e_hitl_multi_0", "execute_in_skill"),
        ("call_e2e_hitl_multi_1", "execute_in_skill"),
    ]
    # Distinct ids AND distinct args keep the two synthetic approval cards from
    # collapsing in mergeInterruptToolCalls on the frontend.
    assert result.tool_calls[0]["args"] != result.tool_calls[1]["args"]
    assert result.tool_calls[0]["args"] == HITL_MULTI_TOOL_CALLS[0]["args"]
    assert result.tool_calls[1]["args"] == HITL_MULTI_TOOL_CALLS[1]["args"]


def test_e2e_scripted_model_stream_preserves_hitl_multi_tool_calls() -> None:
    model = E2EScriptedChatModel(
        model="document-artifact-scripted",
        slow_stream_delay_seconds=0,
    ).bind_tools([{"name": "execute_in_skill"}])

    chunks = list(model.stream([HumanMessage(content=f"{HITL_MULTI_MARKER} 멀티 승인 카드")]))

    tool_calls = [tool_call for chunk in chunks for tool_call in chunk.tool_calls]
    assert [call["id"] for call in tool_calls] == [
        "call_e2e_hitl_multi_0",
        "call_e2e_hitl_multi_1",
    ]


def test_e2e_scripted_model_returns_final_message_after_hitl_multi_tool_results() -> None:
    model = E2EScriptedChatModel(model="document-artifact-scripted").bind_tools(
        [{"name": "execute_in_skill"}]
    )

    result = model.invoke(
        [
            HumanMessage(content=f"{HITL_MULTI_MARKER} 멀티 승인 카드"),
            ToolMessage(
                content="OUTPUT_FILES: moldy-hitl-multi-1.docx",
                tool_call_id="call_e2e_hitl_multi_0",
            ),
            ToolMessage(
                content="OUTPUT_FILES: moldy-hitl-multi-2.docx",
                tool_call_id="call_e2e_hitl_multi_1",
            ),
        ]
    )

    assert result.tool_calls == []
    assert "문서 파일 생성이 완료" in str(result.content)


def test_e2e_scripted_model_does_not_trigger_hitl_for_descriptive_prompt() -> None:
    # A generic descriptive prompt mentioning tools+approval but no execution intent
    # must NOT accidentally fire an execute_in_skill tool call.
    model = E2EScriptedChatModel(model="document-artifact-scripted").bind_tools(
        [{"name": "execute_in_skill"}]
    )

    result = model.invoke([HumanMessage(content="도구 승인 절차를 설명해줘")])

    assert result.tool_calls == []
    assert result.content == "E2E scripted document model is ready."


def test_e2e_scripted_model_emits_ask_user_tool_call_from_openai_tool_schema() -> None:
    model = E2EScriptedChatModel(model="document-artifact-scripted").bind_tools(
        [{"type": "function", "function": {"name": "ask_user"}}]
    )

    result = model.invoke([HumanMessage(content=f"{ASK_USER_FRUIT_MARKER} 과일을 골라줘")])

    assert result.tool_calls[0]["name"] == "ask_user"
    assert result.tool_calls[0]["args"] == ASK_USER_FRUIT_TOOL_ARGS


def test_e2e_scripted_model_returns_after_ask_user_tool_result() -> None:
    model = E2EScriptedChatModel(model="document-artifact-scripted").bind_tools(
        [{"name": "ask_user"}]
    )

    result = model.invoke(
        [
            HumanMessage(content=f"{ASK_USER_FRUIT_MARKER} 과일을 골라줘"),
            ToolMessage(content="🍎 사과", tool_call_id=ASK_USER_FRUIT_TOOL_CALL_ID),
        ]
    )

    assert result.content == ASK_USER_FRUIT_FINAL_CONTENT


def test_e2e_scripted_model_langgraph_v3_subagent_marker_returns_scoped_result() -> None:
    model = E2EScriptedChatModel(model="document-artifact-scripted")

    result = model.invoke([HumanMessage(content="E2E_SUBAGENT summarize scoped work")])

    assert result.content == "E2E subagent scoped result ready."


def test_e2e_scripted_model_streams_langgraph_v3_subagent_marker_in_chunks() -> None:
    model = E2EScriptedChatModel(
        model="document-artifact-scripted",
        slow_stream_delay_seconds=0,
    )

    chunks = list(model.stream([HumanMessage(content="E2E_SUBAGENT summarize scoped work")]))
    content = "".join(str(chunk.content) for chunk in chunks)

    assert len([chunk for chunk in chunks if chunk.content]) >= 3
    assert content == "E2E subagent scoped result ready."


def test_e2e_scripted_model_streams_slow_langgraph_v3_subagent_marker() -> None:
    model = E2EScriptedChatModel(
        model="document-artifact-scripted",
        slow_stream_delay_seconds=0,
    )

    chunks = list(model.stream([HumanMessage(content="E2E_SUBAGENT_SLOW visual matrix")]))
    content = "".join(str(chunk.content) for chunk in chunks)

    assert len([chunk for chunk in chunks if chunk.content]) >= 10
    assert content.startswith("E2E subagent visual matrix:")
    assert "subagent delta still open" in content
    assert content.endswith("ready.")


@pytest.mark.asyncio
async def test_seed_e2e_scripted_model_skips_by_default(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.seed import e2e_scripted_model

    monkeypatch.setattr(e2e_scripted_model.settings, "app_env", "dev")
    monkeypatch.setattr(e2e_scripted_model.settings, "e2e_scripted_model_enabled", False)

    seeded = await seed_e2e_scripted_model(db)

    assert seeded is None


@pytest.mark.asyncio
async def test_seed_e2e_scripted_model_creates_model_when_enabled(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.seed import e2e_scripted_model

    monkeypatch.setattr(e2e_scripted_model.settings, "app_env", "dev")
    monkeypatch.setattr(e2e_scripted_model.settings, "e2e_scripted_model_enabled", True)

    seeded = await seed_e2e_scripted_model(db)
    await db.commit()

    assert seeded is not None
    model = (
        await db.execute(
            select(Model)
            .where(Model.provider == E2E_SCRIPTED_PROVIDER)
            .where(Model.model_name == E2E_SCRIPTED_MODEL_NAME)
        )
    ).scalar_one()
    assert model.display_name == "E2E Scripted Document Model"
    assert model.supports_function_calling is True
