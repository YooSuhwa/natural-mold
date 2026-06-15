from __future__ import annotations

import pytest

from app.services.skill_evaluation_file_adapter import (
    SkillEvaluationFileAdapterError,
    normalize_evaluation_file_payload,
)


def test_normalizes_moldy_eval_file() -> None:
    # Given: a Moldy-native eval file payload.
    payload = {
        "schema_version": 1,
        "name": "Smoke",
        "description": "Basic checks",
        "evals": [
            {
                "input": "Summarize this note.",
                "expected": {"contains": ["summary"]},
                "tags": ["smoke"],
                "metadata": {"priority": "high"},
            }
        ],
    }

    # When: the payload is normalized for SkillEvaluationSet storage.
    normalized = normalize_evaluation_file_payload(payload)

    # Then: Moldy fields are preserved.
    assert normalized == {
        "schema_version": 1,
        "name": "Smoke",
        "description": "Basic checks",
        "evals": [
            {
                "input": "Summarize this note.",
                "expected": {"contains": ["summary"]},
                "tags": ["smoke"],
                "metadata": {"priority": "high", "source_schema": "moldy"},
            }
        ],
    }


def test_normalizes_claude_skill_creator_eval_file() -> None:
    # Given: a Claude Code skill-creator eval file payload.
    payload = {
        "skill_name": "meeting-notes",
        "evals": [
            {
                "id": "case-001",
                "prompt": "Extract action items from this meeting note.",
                "expected_output": "A table of owner, task, and due date.",
                "files": ["inputs/meeting-note.md"],
                "expectations": ["Includes all action items", "Preserves owners"],
                "tags": ["extraction"],
            }
        ],
    }

    # When: the payload is normalized for Moldy.
    normalized = normalize_evaluation_file_payload(payload)

    # Then: Claude fields are mapped into Moldy's eval case contract.
    assert normalized == {
        "schema_version": 1,
        "name": "meeting-notes imported evals",
        "description": None,
        "evals": [
            {
                "input": "Extract action items from this meeting note.",
                "expected": "A table of owner, task, and due date.",
                "tags": ["extraction"],
                "metadata": {
                    "external_id": "case-001",
                    "files": ["inputs/meeting-note.md"],
                    "expectations": ["Includes all action items", "Preserves owners"],
                    "source_schema": "claude_skill_creator",
                },
            }
        ],
    }


def test_rejects_empty_eval_file() -> None:
    # Given: an eval file with no cases.
    payload = {"evals": []}

    # When/Then: normalization rejects it.
    with pytest.raises(SkillEvaluationFileAdapterError, match="at least one case"):
        normalize_evaluation_file_payload(payload)


def test_rejects_eval_file_without_prompt_or_input() -> None:
    # Given: a case that is neither Moldy nor Claude compatible.
    payload = {"evals": [{"expected": "A useful answer."}]}

    # When/Then: normalization rejects the missing task input.
    with pytest.raises(SkillEvaluationFileAdapterError, match="input or prompt"):
        normalize_evaluation_file_payload(payload)


def test_preserves_expectations_and_files_metadata() -> None:
    # Given: a Claude eval case with file and expectation metadata.
    payload = {
        "skill_name": "research",
        "evals": [
            {
                "id": "near-miss",
                "prompt": {"task": "Compare sources"},
                "expected_output": {"format": "bullets"},
                "files": ["sources/a.md", "sources/b.md"],
                "expectations": [{"contains_citation": True}],
            }
        ],
    }

    # When: the payload is normalized.
    normalized = normalize_evaluation_file_payload(payload)

    # Then: portable metadata remains attached to the normalized case.
    metadata = normalized["evals"][0]["metadata"]
    assert metadata["external_id"] == "near-miss"
    assert metadata["files"] == ["sources/a.md", "sources/b.md"]
    assert metadata["expectations"] == [{"contains_citation": True}]
    assert metadata["source_schema"] == "claude_skill_creator"
