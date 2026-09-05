from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.marketplace.skill_runtime import SkillRuntimeDescriptor, SkillToolContext
from app.services.skill_evaluation_llm_payload import skill_payload
from app.services.skill_evaluation_worker_types import SkillEvaluationContext


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


def test_skill_payload_previews_single_file_under_hidden_ancestor(tmp_path: Path) -> None:
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
        runtime_storage_path=skill_file,
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
