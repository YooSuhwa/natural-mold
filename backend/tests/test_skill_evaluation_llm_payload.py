from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

import app.services.skill_evaluation_llm_payload as payload_module
from app.marketplace.skill_runtime import SkillRuntimeDescriptor, SkillToolContext
from app.services.skill_evaluation_llm_payload import (
    json_object_from_text,
    message_text,
    skill_payload,
)
from app.services.skill_evaluation_worker_types import (
    SkillEvaluationContext,
    SkillEvaluationExecutionError,
)


def test_skill_payload_previews_attached_runtime_file(tmp_path: Path) -> None:
    skill_id = uuid.uuid4()
    runtime_root = tmp_path / ".moldy-test-run.preview" / "runtime"
    skill_root = runtime_root / "preview-skill"
    skill_root.mkdir(parents=True)
    skill_content = "Use this skill to produce concise meeting notes.\n"
    (skill_root / "SKILL.md").write_text(skill_content, encoding="utf-8")
    references = skill_root / "references"
    references.mkdir()
    (references / "notes.txt").write_text("Visible reference.\n", encoding="utf-8")
    (skill_root / ".env").write_text("SHOULD_NOT_BE_INCLUDED=1\n", encoding="utf-8")
    hidden_dir = skill_root / ".private"
    hidden_dir.mkdir()
    (hidden_dir / "secret.txt").write_text("hidden secret\n", encoding="utf-8")
    (references / ".hidden.txt").write_text("hidden nested file\n", encoding="utf-8")

    outside_secret = tmp_path / "outside-secret.txt"
    outside_secret.write_text("external secret value\n", encoding="utf-8")
    (skill_root / "outside-link.txt").symlink_to(outside_secret)
    (skill_root / "inside-link.txt").symlink_to(references / "notes.txt")
    (skill_root / "broken-link.txt").symlink_to(tmp_path / "missing.txt")

    descriptor = SkillRuntimeDescriptor(
        id=skill_id,
        slug="preview-skill",
        name="Preview Skill",
        description="Preview payload test skill",
        original_storage_path=skill_root,
        runtime_storage_path=skill_root,
    )
    runtime_context = SkillToolContext(
        thread_id="preview-run",
        output_dir=tmp_path / "outputs",
        runtime_root=runtime_root,
        descriptors={descriptor.slug: descriptor},
    )
    context = SkillEvaluationContext(
        run_id=uuid.uuid4(),
        skill_id=skill_id,
        evaluation_set_id=uuid.uuid4(),
        skill_version="1.0.0",
        skill_content_hash="preview-content-hash",
        evals=[],
        runtime_context=runtime_context,
    )

    payload = skill_payload(context)

    assert payload["skill_id"] == str(skill_id)
    assert payload["skill_version"] == "1.0.0"
    assert payload["skill_content_hash"] == "preview-content-hash"
    assert payload["files"] == [
        {
            "skill_slug": "preview-skill",
            "path": "SKILL.md",
            "content": skill_content,
        },
        {
            "skill_slug": "preview-skill",
            "path": "references/notes.txt",
            "content": "Visible reference.\n",
        },
    ]
    assert "external secret value" not in str(payload)


def test_skill_payload_falls_back_to_original_single_file_under_hidden_ancestor(
    tmp_path: Path,
) -> None:
    skill_id = uuid.uuid4()
    skill_file = tmp_path / ".hidden-parent" / "single-skill" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("Single-file skill.\n", encoding="utf-8")

    descriptor = SkillRuntimeDescriptor(
        id=skill_id,
        slug="single-skill",
        name="Single Skill",
        description="Single file preview test",
        original_storage_path=skill_file,
        runtime_storage_path=tmp_path / "missing-runtime-file",
    )
    runtime_context = SkillToolContext(
        thread_id="single-file-run",
        output_dir=tmp_path / "outputs",
        runtime_root=skill_file.parent,
        descriptors={descriptor.slug: descriptor},
    )
    context = SkillEvaluationContext(
        run_id=uuid.uuid4(),
        skill_id=skill_id,
        evaluation_set_id=uuid.uuid4(),
        skill_version="1.0.0",
        skill_content_hash="single-file-hash",
        evals=[],
        runtime_context=runtime_context,
    )

    payload = skill_payload(context)

    assert payload["files"] == [
        {
            "skill_slug": "single-skill",
            "path": "SKILL.md",
            "content": "Single-file skill.\n",
        }
    ]


@pytest.mark.parametrize("target_kind", ["file", "directory"])
def test_skill_payload_rejects_symlinked_skill_root(
    tmp_path: Path,
    target_kind: str,
) -> None:
    skill_id = uuid.uuid4()
    outside = tmp_path / f"outside-{target_kind}"
    if target_kind == "file":
        outside.write_text("external root secret\n", encoding="utf-8")
    else:
        outside.mkdir()
        (outside / "SKILL.md").write_text("external root secret\n", encoding="utf-8")

    skill_root = tmp_path / "runtime" / "linked-skill"
    skill_root.parent.mkdir()
    skill_root.symlink_to(outside, target_is_directory=target_kind == "directory")
    descriptor = SkillRuntimeDescriptor(
        id=skill_id,
        slug="linked-skill",
        name="Linked Skill",
        description="Symlink root preview test",
        original_storage_path=skill_root,
        runtime_storage_path=skill_root,
    )
    runtime_context = SkillToolContext(
        thread_id="linked-root-run",
        output_dir=tmp_path / "outputs",
        runtime_root=skill_root.parent,
        descriptors={descriptor.slug: descriptor},
    )
    context = SkillEvaluationContext(
        run_id=uuid.uuid4(),
        skill_id=skill_id,
        evaluation_set_id=uuid.uuid4(),
        skill_version="1.0.0",
        skill_content_hash="linked-root-hash",
        evals=[],
        runtime_context=runtime_context,
    )

    payload = skill_payload(context)

    assert payload["files"] == []
    assert "external root secret" not in str(payload)


def test_skill_payload_stops_after_total_preview_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(payload_module, "_MAX_SKILL_FILE_CHARS", 3)
    monkeypatch.setattr(payload_module, "_MAX_TOTAL_SKILL_CHARS", 3)
    skill_id = uuid.uuid4()
    skill_root = tmp_path / "budget-skill"
    skill_root.mkdir()
    (skill_root / "first.txt").write_text("one", encoding="utf-8")
    (skill_root / "second.txt").write_text("two", encoding="utf-8")
    descriptor = SkillRuntimeDescriptor(
        id=skill_id,
        slug="budget-skill",
        name="Budget Skill",
        description="Preview budget test",
        original_storage_path=skill_root,
        runtime_storage_path=skill_root,
    )
    runtime_context = SkillToolContext(
        thread_id="budget-run",
        output_dir=tmp_path / "outputs",
        runtime_root=tmp_path,
        descriptors={descriptor.slug: descriptor},
    )
    context = SkillEvaluationContext(
        run_id=uuid.uuid4(),
        skill_id=skill_id,
        evaluation_set_id=uuid.uuid4(),
        skill_version="1.0.0",
        skill_content_hash="budget-content-hash",
        evals=[],
        runtime_context=runtime_context,
    )

    payload = skill_payload(context)

    assert payload["files"] == [
        {
            "skill_slug": "budget-skill",
            "path": "first.txt",
            "content": "one",
        }
    ]


def test_read_preview_reports_file_read_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview_path = tmp_path / "unreadable.txt"

    def raise_permission_error(
        _path: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        del encoding, errors
        raise PermissionError("preview denied")

    monkeypatch.setattr(Path, "read_text", raise_permission_error)

    preview = payload_module._read_preview(preview_path, remaining=10)

    assert preview == "Error reading file preview: preview denied"


def test_read_preview_truncates_to_available_budget(tmp_path: Path) -> None:
    preview_path = tmp_path / "preview.txt"
    preview_path.write_text("long-content", encoding="utf-8")

    preview = payload_module._read_preview(preview_path, remaining=4)

    assert preview == "long\n...[truncated]"


def test_json_object_from_text_parses_fenced_object() -> None:
    result = json_object_from_text('```json\n{"score": 1, "items": []}\n```')

    assert result == {"score": 1, "items": []}


@pytest.mark.parametrize("text", ["not-json", "[1, 2]"])
def test_json_object_from_text_rejects_invalid_or_non_object_payload(text: str) -> None:
    with pytest.raises(SkillEvaluationExecutionError):
        json_object_from_text(text)


def test_json_object_from_text_rejects_unclosed_json_fence() -> None:
    with pytest.raises(SkillEvaluationExecutionError):
        json_object_from_text('```json\n{"score": 1}')


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (HumanMessage(content="plain response"), "plain response"),
        (
            HumanMessage(content=[{"type": "text", "text": "structured response"}]),
            '[{"type": "text", "text": "structured response"}]',
        ),
    ],
)
def test_message_text_serializes_string_and_structured_content(
    message: HumanMessage,
    expected: str,
) -> None:
    assert message_text(message) == expected
