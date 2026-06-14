from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.agent_runtime.skill_builder.eval_cancellation import (
    EvalCancellationCheckpoint,
    EvalCancellationPhase,
    EvalCancellationProbe,
)
from app.agent_runtime.skill_builder.eval_runner import EvalCaseResult, run_eval_skill_command
from app.marketplace.skill_runtime import SkillToolContext
from app.schemas.skill_builder import JsonValue

_EXECUTE_METADATA_KEY: Final = "execute_in_skill"
_OUTPUT_PREVIEW_MAX_CHARS: Final = 2000


@dataclass(frozen=True, slots=True)
class ExecuteInSkillEvalRequest:
    command: str
    skill_directory: str | None = None


async def deterministic_with_skill_results(
    evals: Sequence[JsonValue],
    cancellation: EvalCancellationProbe,
    runtime_context: SkillToolContext | None,
) -> tuple[list[EvalCaseResult], dict[int, dict[str, JsonValue]]]:
    results: list[EvalCaseResult] = []
    execution_results: dict[int, dict[str, JsonValue]] = {}
    for index, case in enumerate(evals):
        await cancellation.raise_if_cancelled(
            EvalCancellationCheckpoint(EvalCancellationPhase.WITH_SKILL_CASE, case_index=index)
        )
        request = _case_execution_request(case)
        if request is None:
            results.append(EvalCaseResult(case_index=index, passed=True, score=1))
            continue

        execution = await _run_execute_in_skill_case(
            request=request,
            runtime_context=runtime_context,
        )
        passed = execution["status"] == "passed"
        results.append(EvalCaseResult(case_index=index, passed=passed, score=1 if passed else 0))
        execution_results[index] = execution
    return results, execution_results


def has_execution_cases(evals: Sequence[JsonValue]) -> bool:
    return any(_case_execution_request(case) is not None for case in evals)


def _case_execution_request(case: JsonValue) -> ExecuteInSkillEvalRequest | None:
    if not isinstance(case, dict):
        return None
    metadata = case.get("metadata")
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get(_EXECUTE_METADATA_KEY)
    if not isinstance(raw, dict):
        return None
    command = raw.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    skill_directory = raw.get("skill_directory")
    if not isinstance(skill_directory, str) or not skill_directory.strip():
        skill_directory = None
    return ExecuteInSkillEvalRequest(command=command, skill_directory=skill_directory)


async def _run_execute_in_skill_case(
    *,
    request: ExecuteInSkillEvalRequest,
    runtime_context: SkillToolContext | None,
) -> dict[str, JsonValue]:
    if runtime_context is None:
        return {
            "status": "failed",
            "output_preview": "Error: skill runtime context is unavailable.",
        }

    skill_directory = request.skill_directory or _default_skill_directory(runtime_context)
    if skill_directory is None:
        return {
            "status": "failed",
            "output_preview": "Error: no selected skill is mounted for evaluation.",
        }

    output = await run_eval_skill_command(
        runtime_context,
        skill_directory=skill_directory,
        command=request.command,
    )
    output_text = str(output)
    passed = not output_text.lstrip().startswith("Error:")
    return {
        "status": "passed" if passed else "failed",
        "output_preview": _output_preview(output_text),
    }


def _default_skill_directory(runtime_context: SkillToolContext) -> str | None:
    slug = next(iter(runtime_context.descriptors), None)
    if slug is None:
        return None
    return f"/runtime/{runtime_context.thread_id}/skills/{slug}/"


def _output_preview(output: str) -> str:
    normalized = output.strip()
    if len(normalized) <= _OUTPUT_PREVIEW_MAX_CHARS:
        return normalized
    return f"{normalized[:_OUTPUT_PREVIEW_MAX_CHARS]}..."
