from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.e2e_scripted_model import E2EScriptedChatModel
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
