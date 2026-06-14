from __future__ import annotations

import pytest

from app.agent_runtime.skill_builder.eval_schema import (
    SkillEvalSchemaError,
    parse_evals_json,
)


def test_parse_evals_json_accepts_versioned_object() -> None:
    parsed = parse_evals_json(
        "{"
        '"schema_version": 1,'
        '"name": "Smoke",'
        '"evals": ['
        "{"
        '"input": "summarize this note",'
        '"expected": {"contains": ["summary"]},'
        '"tags": ["smoke"]'
        "}"
        "]"
        "}"
    )

    assert parsed.schema_version == 1
    assert parsed.name == "Smoke"
    assert parsed.evals[0].input == "summarize this note"
    assert parsed.evals[0].expected == {"contains": ["summary"]}
    assert parsed.evals[0].tags == ["smoke"]


def test_parse_evals_json_accepts_top_level_case_list() -> None:
    parsed = parse_evals_json('[{"input": {"topic": "pricing"}, "expected": "table"}]')

    assert parsed.evals[0].input == {"topic": "pricing"}
    assert parsed.evals[0].expected == "table"


def test_parse_evals_json_rejects_empty_cases() -> None:
    with pytest.raises(SkillEvalSchemaError, match="schema"):
        parse_evals_json('{"evals": []}')


def test_parse_evals_json_rejects_malformed_json() -> None:
    with pytest.raises(SkillEvalSchemaError, match="invalid JSON"):
        parse_evals_json("{")
