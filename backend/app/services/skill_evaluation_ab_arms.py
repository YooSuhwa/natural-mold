"""Measured two-arm (with/without skill) case execution — Phase 3 §4 (D1).

Per case the ``llm-2`` runner makes up to three single-turn model calls:

1. **with-arm** — the model receives the skill payload (SKILL.md + file
   previews + sandbox execution output for execution cases) and solves the
   case input with it.
2. **without-arm** — the model solves the same input with no skill context
   (the baseline the legacy runner merely *estimated*).
3. **grader** — grades both real answers against ``expected`` and returns the
   per-case verdict JSON.

Every call is timed (wall clock) and its ``usage_metadata`` recorded, so the
resulting benchmark carries measured ``token_delta`` / ``duration_delta_ms``
instead of guesses.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Final

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import settings
from app.schemas.skill_builder import JsonValue
from app.services.skill_evaluation_llm_payload import message_text
from app.services.skill_evaluation_usage import LlmUsageCollector

type JsonObject = dict[str, JsonValue]

# Stable first lines double as detection markers for the E2E scripted model.
WITH_ARM_SYSTEM_PROMPT: Final = "\n".join(
    (
        "You are Moldy's skill evaluation with-skill arm.",
        "Solve the user's task by following the provided skill instructions.",
        "The payload contains the skill files (SKILL.md first) and, when the",
        "case executed a sandbox script, its real output — treat that output",
        "as ground truth produced by the skill.",
        "Answer the task directly and concisely. Do not mention the skill.",
    )
)

WITHOUT_ARM_SYSTEM_PROMPT: Final = "\n".join(
    (
        "You are Moldy's skill evaluation baseline arm.",
        "Solve the user's task with your own general knowledge only.",
        "Answer the task directly and concisely.",
    )
)

AB_GRADER_SYSTEM_PROMPT: Final = "\n".join(
    (
        "You are Moldy's skill evaluation A/B grader.",
        "You receive one eval case with two real answers: `with_skill_answer`",
        "(produced with the skill mounted) and optionally `without_skill_answer`",
        "(produced without it). Grade each answer against `expected`.",
        "Return JSON only:",
        "{",
        '  "case_index": 0,',
        '  "status": "passed" | "failed",',
        '  "score": 0.0,',
        '  "baseline_status": "passed" | "failed",',
        '  "baseline_score": 0.0,',
        '  "grader_feedback": "short reason",',
        '  "evidence": "specific supporting evidence"',
        "}",
        "When `without_skill_answer` is null, omit baseline_status and",
        "baseline_score entirely — never guess a baseline you did not see.",
    )
)

_ANSWER_PREVIEW_MAX_CHARS: Final = 500


@dataclass(frozen=True, slots=True)
class ArmRun:
    """One timed, usage-measured model call."""

    answer: str
    tokens_in: int
    tokens_out: int
    duration_ms: int


@dataclass(frozen=True, slots=True)
class CaseArmMeasurement:
    case_index: int
    with_run: ArmRun
    without_run: ArmRun | None

    def case_row_metrics(self) -> JsonObject:
        """Measured per-case metrics in the normalized case-row vocabulary.

        The schema-v2 case normalizer (``skill_evaluation_result_cases``)
        already reserves ``duration_ms``/``tokens``/``baseline_*`` slots —
        legacy runners left them None; the llm-2 runner fills them for real,
        which lights up the benchmark/kpi/comparison deltas downstream.
        """

        metrics: JsonObject = {
            "duration_ms": self.with_run.duration_ms,
            "tokens": self.with_run.tokens_in + self.with_run.tokens_out,
            "with_answer_preview": _preview(self.with_run.answer),
        }
        if self.without_run is not None:
            metrics["baseline_duration_ms"] = self.without_run.duration_ms
            metrics["baseline_tokens"] = self.without_run.tokens_in + self.without_run.tokens_out
            metrics["without_answer_preview"] = _preview(self.without_run.answer)
        return metrics


async def run_arm(
    model: BaseChatModel,
    *,
    system_prompt: str,
    user_content: str,
    collector: LlmUsageCollector,
    timeout_seconds: float | None = None,
) -> ArmRun:
    timeout = timeout_seconds or settings.skill_evaluation_case_timeout_seconds
    started = time.monotonic()
    response = await asyncio.wait_for(
        model.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_content)]),
        timeout=timeout,
    )
    duration_ms = int((time.monotonic() - started) * 1000)
    tokens_in, tokens_out = collector.add_response(response)
    return ArmRun(
        answer=message_text(response),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        duration_ms=duration_ms,
    )


def with_arm_user_content(
    *,
    case: JsonValue,
    skill_payload: JsonObject,
    execution_result: JsonObject | None,
) -> str:
    return json.dumps(
        {
            "task": _case_field(case, "input"),
            "skill": skill_payload,
            "sandbox_execution": execution_result,
        },
        ensure_ascii=False,
    )


def without_arm_user_content(*, case: JsonValue) -> str:
    return json.dumps({"task": _case_field(case, "input")}, ensure_ascii=False)


def grader_user_content(
    *,
    case_index: int,
    case: JsonValue,
    measurement: CaseArmMeasurement,
    execution_result: JsonObject | None,
) -> str:
    return json.dumps(
        {
            "case_index": case_index,
            "input": _case_field(case, "input"),
            "expected": _case_field(case, "expected"),
            "with_skill_answer": measurement.with_run.answer,
            "without_skill_answer": (
                measurement.without_run.answer if measurement.without_run else None
            ),
            "sandbox_execution": execution_result,
        },
        ensure_ascii=False,
    )


def measured_benchmark_extras(*, baseline_enabled: bool) -> JsonObject:
    """Run-level measured markers layered onto ``aggregate_benchmark``.

    Deltas (token/duration/pass-rate) are deliberately NOT provided here —
    ``normalize_skill_evaluation_result`` derives them from the measured
    per-case metrics, keeping a single source of truth.
    """

    return {"measured": True, "baseline_skipped": not baseline_enabled}


def _case_field(case: JsonValue, field_name: str) -> JsonValue:
    if isinstance(case, dict):
        return case.get(field_name)
    return None


def _preview(answer: str) -> str:
    normalized = answer.strip()
    if len(normalized) <= _ANSWER_PREVIEW_MAX_CHARS:
        return normalized
    return f"{normalized[:_ANSWER_PREVIEW_MAX_CHARS]}..."
