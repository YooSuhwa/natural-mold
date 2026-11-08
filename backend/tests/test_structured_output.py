"""Workflow W0: the output schema subset, the per-run response format, the outcomes."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError

from app.agent_runtime import agent_invoke_runner
from app.agent_runtime.runtime_agent_builder import build_agent
from app.agent_runtime.runtime_agent_preparation import _structured_output_kwargs
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.structured_output import (
    MAX_DEPTH,
    OutputSchemaError,
    StructuredOutcome,
    StructuredOutputExhausted,
    check_output_schema,
    output_model,
    response_format_for,
    structured_outcome,
)
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ["refund", "bug", "other"]},
        "urgent": {"type": "boolean"},
        "score": {"type": "number"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "customer": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "orders": {"type": "integer"}},
            "required": ["name"],
        },
    },
    "required": ["category", "urgent"],
}
GOOD = {"category": "refund", "urgent": True, "customer": {"name": "Kim"}}


def _cfg() -> AgentConfig:
    return AgentConfig(
        provider="openai",
        model_name="m",
        api_key=None,
        base_url=None,
        system_prompt="",
        tools_config=[],
        thread_id="00000000-0000-0000-0000-000000000001",
        org_context=unit_context(TEST_USER_ID),
    )


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> ToolModel:
        return self


def _call(args: dict[str, Any], call_id: str = "c1") -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": "WorkflowOutput", "args": args, "id": call_id}]
    )


def _nested(depth: int) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "string"}
    for _ in range(depth):
        node = {"type": "object", "properties": {"x": node}}
    return node


def test_the_subset_accepts_the_editor_shapes() -> None:
    assert check_output_schema(SCHEMA) == SCHEMA
    check_output_schema(_nested(MAX_DEPTH))


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        (lambda s: s.update(type="array"), "root must be type object"),
        (lambda s: s["properties"].update(bad={"type": "null"}), "unsupported type"),
        (lambda s: s["properties"].update(bad={"type": "string", "pattern": "x"}), "unsupported"),
        (lambda s: s["properties"].update({"1bad": {"type": "string"}}), "identifier"),
        (lambda s: s.update(required=["missing"]), "required names"),
        (lambda s: s.update(required=["urgent", "urgent"]), "required names"),
        (lambda s: s["properties"]["tags"].pop("items"), "array needs items"),
        (lambda s: s["properties"]["score"].update(enum=["1"]), "enum is only for strings"),
        (lambda s: s["properties"]["category"].update(enum=[]), "enum must be"),
        (lambda s: s["properties"]["category"].update(enum=["a", "a"]), "enum must be"),
        (lambda s: s["properties"]["customer"].update(properties={}), "needs properties"),
        (lambda s: s["properties"]["score"].update(description="x" * 501), "description"),
    ],
)
def test_the_subset_rejects_what_the_editor_cannot_build(change: Any, fragment: str) -> None:
    schema = copy.deepcopy(SCHEMA)
    change(schema)
    with pytest.raises(OutputSchemaError, match=fragment):
        check_output_schema(schema)


def test_depth_size_and_width_are_bounded() -> None:
    with pytest.raises(OutputSchemaError, match="deeper"):
        check_output_schema(_nested(MAX_DEPTH + 1))
    wide = {"type": "object", "properties": {f"f{i}": {"type": "string"} for i in range(51)}}
    with pytest.raises(OutputSchemaError, match="more than 50"):
        check_output_schema(wide)
    big = {"type": "object", "properties": {"a": {"type": "string", "description": "x" * 500}}}
    big["properties"].update(
        {f"f{i}": {"type": "string", "description": "y" * 400} for i in range(45)}
    )
    with pytest.raises(OutputSchemaError, match="larger than"):
        check_output_schema(big)
    with pytest.raises(OutputSchemaError):
        check_output_schema(["not", "an", "object"])


def test_the_model_takes_exactly_the_schema() -> None:
    model = output_model(SCHEMA)
    assert model.model_validate(GOOD).model_dump(mode="json")["customer"]["orders"] is None
    for bad in (
        {"urgent": True},  # required missing
        {**GOOD, "category": "spam"},  # outside the enum
        {**GOOD, "extra": 1},  # unknown key
        {**GOOD, "customer": {"name": "Kim", "x": 1}},  # unknown nested key
        {**GOOD, "tags": "a"},  # not a list
    ):
        with pytest.raises(ValidationError):
            model.model_validate(bad)


def test_outcomes_from_a_finished_state() -> None:
    model = output_model(SCHEMA)
    assert structured_outcome({"structured_response": model.model_validate(GOOD)}, SCHEMA) == (
        StructuredOutcome("ok", model.model_validate(GOOD).model_dump(mode="json"))
    )
    # A state value is checked again, never trusted.
    assert structured_outcome({"structured_response": {"urgent": 1}}, SCHEMA).status == (
        "schema_invalid"
    )
    cut = AIMessage(content="...", response_metadata={"finish_reason": "length"})
    assert structured_outcome({"messages": [cut]}, SCHEMA).status == "truncated"
    stop = AIMessage(content="...", response_metadata={"stop_reason": "max_tokens"})
    assert structured_outcome({"messages": [stop]}, SCHEMA).status == "truncated"
    refused = AIMessage(content="", additional_kwargs={"refusal": "no"})
    assert structured_outcome({"messages": [refused]}, SCHEMA).status == "refused"
    anthropic = AIMessage(content="", response_metadata={"stop_reason": "refusal"})
    assert structured_outcome({"messages": [anthropic]}, SCHEMA).status == "refused"
    plain = AIMessage(content="just text")
    assert structured_outcome({"messages": [HumanMessage("q"), plain]}, SCHEMA).status == (
        "schema_invalid"
    )


def test_the_retry_budget_is_per_run() -> None:
    first, second = response_format_for(SCHEMA), response_format_for(SCHEMA)
    handle = first.handle_errors
    assert callable(handle)
    assert "did not match" in str(handle(ValueError("x")))
    with pytest.raises(StructuredOutputExhausted):
        handle(ValueError("x"))
    assert callable(second.handle_errors)
    assert "did not match" in str(second.handle_errors(ValueError("x")))


def _agent(*responses: AIMessage) -> Any:
    model = ToolModel(responses=list(responses))
    return build_agent(model, [], "Answer.", response_format=response_format_for(SCHEMA))


@pytest.mark.asyncio
async def test_a_built_agent_returns_the_validated_answer() -> None:
    result = await _agent(_call(GOOD)).ainvoke({"messages": [HumanMessage("refund?")]})
    assert structured_outcome(result, SCHEMA).value == {
        **GOOD,
        "score": None,
        "tags": None,
        "customer": {"name": "Kim", "orders": None},
    }


@pytest.mark.asyncio
async def test_one_miss_is_retried_and_a_second_miss_fails() -> None:
    retried = await _agent(_call({"urgent": "maybe"}), _call(GOOD, "c2")).ainvoke(
        {"messages": [HumanMessage("q")]}
    )
    assert structured_outcome(retried, SCHEMA).status == "ok"
    with pytest.raises(StructuredOutputExhausted):
        await _agent(_call({"urgent": "maybe"}), _call({"x": 1}, "c2")).ainvoke(
            {"messages": [HumanMessage("q")]}
        )


def test_only_a_schema_run_gets_a_response_format() -> None:
    plain = _cfg()
    assert _structured_output_kwargs(plain) == {}
    plain.output_schema = SCHEMA
    assert set(_structured_output_kwargs(plain)) == {"response_format"}
    plain.output_schema = {"type": "string"}
    with pytest.raises(OutputSchemaError):
        _structured_output_kwargs(plain)


class _Graph:
    def __init__(self, result: Any) -> None:
        self.result = result

    async def ainvoke(self, *_: Any, **__: Any) -> Any:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def structured_cfg() -> AgentConfig:
    cfg = _cfg()
    cfg.output_schema = SCHEMA
    cfg.secret_values = {"sk-secret-value-123"}
    return cfg


def _patch_graph(monkeypatch: pytest.MonkeyPatch, result: Any) -> None:
    async def prepare(*_: Any, **__: Any) -> Any:
        return _Graph(result), [HumanMessage("q")], {"configurable": {}}

    from app.agent_runtime import agent_stream_runner

    monkeypatch.setattr(agent_stream_runner, "_prepare_agent", prepare)


@pytest.mark.asyncio
async def test_structured_invoke_redacts_the_value(
    monkeypatch: pytest.MonkeyPatch, structured_cfg: AgentConfig
) -> None:
    leaked = {**GOOD, "customer": {"name": "sk-secret-value-123"}}
    _patch_graph(monkeypatch, {"structured_response": leaked, "messages": []})
    result = await agent_invoke_runner.execute_agent_invoke_structured(structured_cfg, [])
    assert result.outcome.status == "ok"
    assert result.outcome.value is not None
    assert "sk-secret-value-123" not in str(result.outcome.value)


@pytest.mark.asyncio
async def test_structured_invoke_maps_an_exhausted_retry(
    monkeypatch: pytest.MonkeyPatch, structured_cfg: AgentConfig
) -> None:
    _patch_graph(monkeypatch, StructuredOutputExhausted("x"))
    result = await agent_invoke_runner.execute_agent_invoke_structured(structured_cfg, [])
    assert result.outcome == StructuredOutcome("schema_invalid")


@pytest.mark.asyncio
async def test_structured_invoke_needs_a_schema(structured_cfg: AgentConfig) -> None:
    structured_cfg.output_schema = None
    with pytest.raises(ValueError, match="output_schema"):
        await agent_invoke_runner.execute_agent_invoke_structured(structured_cfg, [])
