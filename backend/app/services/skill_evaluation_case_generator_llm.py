from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder.agent import (
    SkillBuilderChatModel,
    build_skill_builder_chat_model,
)
from app.models.skill import Skill
from app.schemas.skill_builder import JsonValue
from app.services.skill_evaluation_file_adapter import (
    SkillEvaluationFileAdapterError,
    normalize_evaluation_file_payload,
)
from app.services.skill_evaluation_llm_payload import json_object_from_text, message_text
from app.services.skill_evaluation_worker_types import SkillEvaluationExecutionError
from app.storage.paths import resolve_data_path

type JsonObject = dict[str, JsonValue]
type ModelBuilder = Callable[[AsyncSession], Awaitable[SkillBuilderChatModel]]

_DEFAULT_CASE_COUNT: Final = 3
_MAX_GENERATED_CASES: Final = 5
_MAX_FILE_PREVIEWS: Final = 6
_MAX_FILE_CHARS: Final = 4000
_GENERATOR_SYSTEM_PROMPT: Final = "\n".join(
    (
        "You generate portable smoke evaluation cases for Moldy skills.",
        "Return JSON only. Do not include secrets or private user data.",
        "Create observable cases that can later run in quick or precision mode.",
        "Use this shape:",
        "{",
        '  "name": "Generated smoke evaluation",',
        '  "description": "Smoke tests generated from the installed skill package.",',
        '  "evals": [',
        "    {",
        '      "input": "user-like task prompt",',
        '      "expected": "observable expected behavior",',
        '      "tags": ["smoke"],',
        '      "metadata": {"expectations": ["observable expectation"]}',
        "    }",
        "  ]",
        "}",
    )
)


class SkillEvaluationCaseGenerationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GeneratedSkillEvaluationPayload:
    payload: JsonObject
    model_name: str


async def generate_skill_smoke_eval_payload(
    db: AsyncSession,
    *,
    skill: Skill,
    model_builder: ModelBuilder = build_skill_builder_chat_model,
    case_count: int = _DEFAULT_CASE_COUNT,
) -> GeneratedSkillEvaluationPayload:
    built_model = await model_builder(db)
    response = await built_model.model.ainvoke(
        [
            SystemMessage(content=_GENERATOR_SYSTEM_PROMPT),
            HumanMessage(
                content=json.dumps(
                    {
                        "skill": _skill_payload(skill),
                        "requested_case_count": min(case_count, _MAX_GENERATED_CASES),
                    },
                    ensure_ascii=False,
                )
            ),
        ]
    )
    payload = _normalize_generated_payload(message_text(response))
    return GeneratedSkillEvaluationPayload(payload=payload, model_name=built_model.model_name)


def _normalize_generated_payload(text: str) -> JsonObject:
    try:
        raw = json_object_from_text(text)
        payload = normalize_evaluation_file_payload(raw)
    except (SkillEvaluationExecutionError, SkillEvaluationFileAdapterError) as exc:
        raise SkillEvaluationCaseGenerationError(str(exc)) from exc
    evals = _case_list(payload)
    payload["evals"] = [_mark_generated(case) for case in evals[:_MAX_GENERATED_CASES]]
    return payload


def _skill_payload(skill: Skill) -> JsonObject:
    return {
        "id": str(skill.id),
        "name": skill.name,
        "description": skill.description,
        "version": skill.version,
        "kind": skill.kind,
        "files": _skill_file_previews(skill),
    }


def _skill_file_previews(skill: Skill) -> list[JsonObject]:
    if not skill.storage_path:
        return []
    root = resolve_data_path(skill.storage_path)
    paths = _preview_paths(root)
    previews: list[JsonObject] = []
    for path in paths[:_MAX_FILE_PREVIEWS]:
        previews.append(
            {
                "path": _relative_preview_path(root, path),
                "content": _read_limited_text(path),
            }
        )
    return previews


def _preview_paths(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    skill_md = root / "SKILL.md"
    paths = [skill_md] if skill_md.is_file() else []
    paths.extend(
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path != skill_md
        and not any(part.startswith(".") for part in path.parts)
    )
    return paths


def _relative_preview_path(root: Path, path: Path) -> str:
    base = root if root.is_dir() else root.parent
    return str(path.relative_to(base))


def _read_limited_text(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"Error reading file preview: {exc}"
    if len(content) <= _MAX_FILE_CHARS:
        return content
    return f"{content[:_MAX_FILE_CHARS]}\n...[truncated]"


def _case_list(payload: JsonObject) -> list[JsonObject]:
    evals = payload.get("evals")
    if not isinstance(evals, list):
        raise SkillEvaluationCaseGenerationError("generated payload missing evals")
    cases: list[JsonObject] = []
    for item in evals:
        if not isinstance(item, dict):
            raise SkillEvaluationCaseGenerationError("generated eval cases must be objects")
        cases.append(dict(item))
    if not cases:
        raise SkillEvaluationCaseGenerationError("generated payload has no eval cases")
    return cases


def _mark_generated(case: JsonObject) -> JsonObject:
    metadata = case.get("metadata")
    normalized_metadata = dict(metadata) if isinstance(metadata, dict) else {}
    normalized_metadata["generated"] = True
    normalized_metadata.setdefault("source_schema", "moldy")
    return {**case, "metadata": normalized_metadata}
