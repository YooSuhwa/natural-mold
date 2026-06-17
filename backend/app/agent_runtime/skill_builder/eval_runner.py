from __future__ import annotations

import shlex
from dataclasses import dataclass
from math import sqrt
from pathlib import Path
from typing import Final

import anyio

from app.agent_runtime.skill_builder.eval_limits import MAX_EVAL_COMMAND_CHARS
from app.agent_runtime.skill_executor import _create_skill_execute_tool
from app.marketplace.skill_runtime import SkillToolContext
from app.schemas.skill_builder import JsonValue

REQUIRED_GRADER_RESULT_KEYS: Final = (
    "expectations",
    "summary",
    "execution_metrics",
    "timing",
    "claims",
    "eval_feedback",
)
_POLICY_PROBE_MARKER: Final = "MOLDY_EVAL_POLICY_OK"
_POLICY_PROBE_OUTPUT_FILE: Final = "eval-policy-probe.txt"
_POLICY_PROBE_SCRIPT: Final = "scripts/moldy_eval_policy_probe.py"
_POLICY_PROBE_CODE: Final = "\n".join(
    (
        "import os",
        "import pathlib",
        "cwd = pathlib.Path.cwd().resolve()",
        "home = pathlib.Path(os.environ['HOME']).resolve()",
        "pythonpath = pathlib.Path(os.environ['PYTHONPATH']).resolve()",
        "output_dir = pathlib.Path(os.environ['SKILL_OUTPUT_DIR']).resolve()",
        "outputs_dir = pathlib.Path(os.environ['OUTPUTS_DIR']).resolve()",
        "if home != cwd or pythonpath != cwd or output_dir != outputs_dir:",
        "    raise SystemExit('scoped env mismatch')",
        f"(output_dir / '{_POLICY_PROBE_OUTPUT_FILE}').write_text('ok')",
        f"print('{_POLICY_PROBE_MARKER}')",
    )
)


class GraderResultError(ValueError):
    pass


class EvalRuntimePolicyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EvalOutputDirs:
    root: Path
    with_skill: Path
    without_skill: Path


@dataclass(frozen=True, slots=True)
class EvalCaseResult:
    case_index: int
    passed: bool
    score: float


def prepare_eval_output_dirs(root: Path, *, run_id: str) -> EvalOutputDirs:
    run_root = root / run_id
    with_skill = run_root / "with-skill"
    without_skill = run_root / "without-skill"
    with_skill.mkdir(parents=True, exist_ok=True)
    without_skill.mkdir(parents=True, exist_ok=True)
    return EvalOutputDirs(root=run_root, with_skill=with_skill, without_skill=without_skill)


async def run_eval_skill_command(
    ctx: SkillToolContext,
    *,
    skill_slug: str | None = None,
    skill_directory: str | None = None,
    command: str,
) -> str:
    if len(command) > MAX_EVAL_COMMAND_CHARS:
        return f"Error: evaluation command exceeds {MAX_EVAL_COMMAND_CHARS} characters."
    if (skill_slug is None) == (skill_directory is None):
        raise EvalRuntimePolicyError("provide exactly one skill slug or skill directory")
    tool = _create_skill_execute_tool(ctx)
    coroutine = tool.coroutine
    if not callable(coroutine):
        raise EvalRuntimePolicyError("execute_in_skill coroutine is unavailable")
    directory = skill_directory or f"/runtime/{ctx.thread_id}/skills/{skill_slug}/"
    result = await coroutine(
        skill_directory=directory,
        command=command,
    )
    return str(result)


async def run_eval_runtime_policy_probe(ctx: SkillToolContext) -> None:
    if not ctx.descriptors:
        return
    skill_slug, descriptor = next(iter(ctx.descriptors.items()))
    await anyio.to_thread.run_sync(
        _write_policy_probe_script,
        descriptor.runtime_storage_path / _POLICY_PROBE_SCRIPT,
    )
    command = f"python {shlex.quote(_POLICY_PROBE_SCRIPT)}"
    result = await run_eval_skill_command(ctx, skill_slug=skill_slug, command=command)
    if result.startswith("Error:"):
        raise EvalRuntimePolicyError(result.strip())
    if _POLICY_PROBE_MARKER not in {line.strip() for line in result.splitlines()}:
        raise EvalRuntimePolicyError("evaluation runtime policy probe did not complete")


def aggregate_benchmark(
    *,
    with_skill: list[EvalCaseResult],
    without_skill: list[EvalCaseResult],
) -> dict[str, JsonValue]:
    with_pass_rate = _pass_rate(with_skill)
    without_pass_rate = _pass_rate(without_skill)
    with_stats = _score_stats(with_skill)
    without_stats = _score_stats(without_skill)
    return {
        "case_count": max(len(with_skill), len(without_skill)),
        "with_skill_pass_rate": with_pass_rate,
        "without_skill_pass_rate": without_pass_rate,
        "pass_rate_delta": round(with_pass_rate - without_pass_rate, 6),
        "with_skill_mean_score": with_stats["mean"],
        "without_skill_mean_score": without_stats["mean"],
        "mean_score_delta": round(with_stats["mean"] - without_stats["mean"], 6),
        "with_skill_min_score": with_stats["min"],
        "without_skill_min_score": without_stats["min"],
        "with_skill_max_score": with_stats["max"],
        "without_skill_max_score": without_stats["max"],
        "with_skill_stddev_score": with_stats["stddev"],
        "without_skill_stddev_score": without_stats["stddev"],
    }


def _write_policy_probe_script(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_POLICY_PROBE_CODE, encoding="utf-8")


def validate_grader_result(result: dict[str, JsonValue]) -> dict[str, JsonValue]:
    missing = [key for key in REQUIRED_GRADER_RESULT_KEYS if key not in result]
    if missing:
        raise GraderResultError(f"grader result missing keys: {', '.join(missing)}")
    claims = result["claims"]
    if not isinstance(claims, list) or not claims:
        raise GraderResultError("grader result must include at least one evidence claim")
    feedback = result["eval_feedback"]
    if not isinstance(feedback, list):
        raise GraderResultError("grader result eval_feedback must be a list")
    return result


def _pass_rate(results: list[EvalCaseResult]) -> float:
    if not results:
        return 0
    passed = sum(1 for result in results if result.passed)
    return round(passed / len(results), 6)


def _score_stats(results: list[EvalCaseResult]) -> dict[str, float]:
    if not results:
        return {"mean": 0, "min": 0, "max": 0, "stddev": 0}
    scores = [result.score for result in results]
    mean = sum(scores) / len(scores)
    variance = sum((score - mean) ** 2 for score in scores) / len(scores)
    return {
        "mean": round(mean, 6),
        "min": round(min(scores), 6),
        "max": round(max(scores), 6),
        "stddev": round(sqrt(variance), 6),
    }
