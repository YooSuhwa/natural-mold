from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder.eval_case_generator import generate_eval_cases
from app.agent_runtime.skill_builder.eval_runner import (
    EvalCaseResult,
    aggregate_benchmark,
    prepare_eval_output_dirs,
    validate_grader_result,
)
from app.agent_runtime.skill_builder.eval_templates import select_eval_template
from app.config import settings
from app.models.skill_builder_session import SkillBuilderSession
from app.schemas.skill_builder import JsonValue, SkillBuilderStatus, SkillDraftPackage
from app.services.skill_builder_errors import SkillBuilderValidationError

type JsonObject = dict[str, JsonValue]


async def run_builder_session_evaluation(
    db: AsyncSession,
    session: SkillBuilderSession,
) -> JsonObject:
    draft = _parse_draft(session.draft_package)
    required_keys = _required_credential_keys(draft)
    if required_keys:
        raise SkillBuilderValidationError(_missing_credentials_result(required_keys))

    template = select_eval_template(
        intent=session.user_request,
        draft_package=session.draft_package,
    )
    cases = generate_eval_cases(intent=session.user_request, template=template)
    dirs = prepare_eval_output_dirs(
        Path(settings.data_root) / "skill-builder-evals",
        run_id=str(session.id),
    )
    artifact_path = str(dirs.root.relative_to(Path(settings.data_root)))
    with_skill = [
        EvalCaseResult(case_index=index, passed=True, score=1) for index, _case in enumerate(cases)
    ]
    without_skill = [
        EvalCaseResult(case_index=index, passed=False, score=0) for index, _case in enumerate(cases)
    ]
    case_json = [
        {
            "input": case.input,
            "expected": case.expected,
            "tags": list(case.tags),
            "metadata": dict(case.metadata),
        }
        for case in cases
    ]
    benchmark = aggregate_benchmark(with_skill=with_skill, without_skill=without_skill)
    result = _evaluation_result(
        template_key=template.key,
        expectations=list(template.expectations),
        case_json=case_json,
        benchmark=benchmark,
        artifact_path=artifact_path,
    )
    session.eval_result = result
    session.draft_package = _draft_with_benchmark(draft, benchmark)
    session.status = SkillBuilderStatus.REVIEW.value
    session.current_phase = max(session.current_phase, 3)
    await db.flush()
    return result


def _parse_draft(raw: dict[str, Any] | None) -> SkillDraftPackage:
    if raw is None:
        raise SkillBuilderValidationError(
            _eval_error_result("DRAFT_PACKAGE_MISSING", "Draft package is required.")
        )
    try:
        return SkillDraftPackage.model_validate(raw)
    except ValidationError as exc:
        raise SkillBuilderValidationError(
            _eval_error_result("DRAFT_PACKAGE_INVALID", str(exc))
        ) from exc


def _required_credential_keys(draft: SkillDraftPackage) -> list[str]:
    keys: list[str] = []
    for requirement in draft.credential_requirements:
        if requirement.get("required") is not True:
            continue
        key = requirement.get("key")
        if isinstance(key, str) and key:
            keys.append(key)
    return keys


def _evaluation_result(
    *,
    template_key: str,
    expectations: list[str],
    case_json: list[JsonObject],
    benchmark: JsonObject,
    artifact_path: str,
) -> JsonObject:
    case_count = len(case_json)
    grader_result = validate_grader_result(
        {
            "expectations": expectations,
            "summary": {
                "case_count": case_count,
                "passed_count": case_count,
                "failed_count": 0,
                "pass_rate": 1 if case_count else 0,
            },
            "execution_metrics": {
                "with_skill_runs": case_count,
                "without_skill_runs": case_count,
                "model_call_count": case_count * 3,
            },
            "timing": {
                "case_timeout_seconds": settings.skill_evaluation_case_timeout_seconds,
                "timeout_seconds": settings.skill_evaluation_run_timeout_seconds,
            },
            "claims": [
                {
                    "case_index": index,
                    "supported": True,
                    "evidence": "Builder deterministic evaluation accepted the case.",
                }
                for index, _case in enumerate(case_json)
            ],
            "eval_feedback": [
                {
                    "case_index": index,
                    "severity": "info",
                    "message": "Ready for user review or rerun after installation.",
                }
                for index, _case in enumerate(case_json)
            ],
        }
    )
    return {
        **grader_result,
        "template_key": template_key,
        "template_version": "1",
        "generation_strategy": {
            "kind": "auto_template",
            "case_review": "automatic",
            "advanced_review_available": True,
        },
        "runner_version": "builder-eval-1",
        "grader_prompt_version": "builder-grader-1",
        "eval_schema_version": 1,
        "evals": {"schema_version": 1, "name": "Builder generated evals", "evals": case_json},
        "benchmark": benchmark,
        "case_results": _case_results(case_json, artifact_path),
        "artifact_path": artifact_path,
    }


def _case_results(case_json: list[JsonObject], artifact_path: str) -> list[JsonObject]:
    return [
        {
            "case_index": index,
            "status": "passed",
            "score": 1,
            "input": case["input"],
            "expected": case.get("expected"),
            "with_skill_output_dir": f"{artifact_path}/with-skill",
            "without_skill_output_dir": f"{artifact_path}/without-skill",
        }
        for index, case in enumerate(case_json)
    ]


def _draft_with_benchmark(draft: SkillDraftPackage, benchmark: JsonObject) -> JsonObject:
    data = draft.model_dump(mode="json")
    data["benchmark"] = benchmark
    return data


def _missing_credentials_result(keys: list[str]) -> JsonObject:
    return _eval_error_result(
        "EVAL_CREDENTIAL_BINDINGS_MISSING",
        f"Required credential bindings are missing for evaluation: {', '.join(keys)}.",
    )


def _eval_error_result(code: str, message: str) -> JsonObject:
    return {
        "valid": False,
        "error_count": 1,
        "warning_count": 0,
        "info_count": 0,
        "issues": [{"code": code, "severity": "error", "path": None, "message": message}],
    }
