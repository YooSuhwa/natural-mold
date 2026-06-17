from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from langchain_core.messages import BaseMessage

from app.schemas.skill_builder import JsonValue
from app.services.skill_evaluation_worker_types import (
    SkillEvaluationContext,
    SkillEvaluationExecutionError,
)

type JsonObject = dict[str, JsonValue]

GRADER_SYSTEM_PROMPT: Final = "\n".join(
    (
        "You are Moldy's skill evaluation grader.",
        "Grade whether the provided skill package would satisfy each eval case.",
        "Use the skill instructions and any sandbox execution output as evidence.",
        "Also estimate the baseline result without the skill. Return JSON only:",
        "{",
        '  "case_results": [',
        "    {",
        '      "case_index": 0,',
        '      "status": "passed" | "failed",',
        '      "score": 0.0,',
        '      "baseline_status": "passed" | "failed",',
        '      "baseline_score": 0.0,',
        '      "grader_feedback": "short reason",',
        '      "evidence": "specific supporting evidence"',
        "    }",
        "  ],",
        '  "claims": [{"case_index": 0, "supported": true, "evidence": "..."}],',
        (
            '  "eval_feedback": [{"case_index": 0, "severity": "info|warning|error", '
            '"message": "..."}]'
        ),
        "}",
    )
)
_MAX_SKILL_FILES: Final = 12
_MAX_SKILL_FILE_CHARS: Final = 6000
_MAX_TOTAL_SKILL_CHARS: Final = 24000


def skill_payload(context: SkillEvaluationContext) -> JsonObject:
    return {
        "skill_id": str(context.skill_id),
        "skill_version": context.skill_version,
        "skill_content_hash": context.skill_content_hash,
        "files": _skill_file_previews(context),
    }


def json_object_from_text(text: str) -> JsonObject:
    try:
        raw = json.loads(_json_payload(text))
    except json.JSONDecodeError as exc:
        raise SkillEvaluationExecutionError(f"LLM grader returned invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise SkillEvaluationExecutionError("LLM grader result must be a JSON object")
    return raw


def message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False)


def _skill_file_previews(context: SkillEvaluationContext) -> list[JsonObject]:
    previews: list[JsonObject] = []
    remaining = _MAX_TOTAL_SKILL_CHARS
    for descriptor in context.runtime_context.descriptors.values():
        root = descriptor.runtime_storage_path
        if not root.exists():
            root = descriptor.original_storage_path
        for path in _iter_skill_files(root):
            if len(previews) >= _MAX_SKILL_FILES or remaining <= 0:
                return previews
            content = _read_preview(path, remaining)
            remaining -= len(content)
            previews.append(
                {
                    "skill_slug": descriptor.slug,
                    "path": str(path.relative_to(root if root.is_dir() else root.parent)),
                    "content": content,
                }
            )
    return previews


def _iter_skill_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    if not root.exists():
        return []
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and not any(part.startswith(".") for part in path.parts)
    ]


def _read_preview(path: Path, remaining: int) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"Error reading file preview: {exc}"
    limit = min(_MAX_SKILL_FILE_CHARS, remaining)
    if len(content) <= limit:
        return content
    return f"{content[:limit]}\n...[truncated]"


def _json_payload(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped
