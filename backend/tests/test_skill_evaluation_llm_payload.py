from __future__ import annotations

import uuid
from pathlib import Path

from app.marketplace.skill_runtime import SkillRuntimeDescriptor, SkillToolContext
from app.services.skill_evaluation_llm_payload import skill_payload
from app.services.skill_evaluation_worker_types import SkillEvaluationContext


def test_skill_payload_previews_attached_runtime_file(tmp_path: Path) -> None:
    skill_id = uuid.uuid4()
    runtime_root = tmp_path / "runtime"
    skill_root = runtime_root / "preview-skill"
    skill_root.mkdir(parents=True)
    skill_content = "Use this skill to produce concise meeting notes.\n"
    (skill_root / "SKILL.md").write_text(skill_content, encoding="utf-8")

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
        }
    ]
