from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder.agent import (
    SkillBuilderChatModel,
    build_skill_builder_chat_model,
)
from app.agent_runtime.skill_builder.deterministic_eval_execution import (
    deterministic_with_skill_results,
    has_execution_cases,
)
from app.agent_runtime.skill_builder.eval_cancellation import (
    EvalCancellationCheckpoint,
    EvalCancellationPhase,
)
from app.agent_runtime.skill_builder.eval_runner import (
    aggregate_benchmark,
    run_eval_runtime_policy_probe,
)
from app.services.skill_evaluation_llm_payload import (
    GRADER_SYSTEM_PROMPT,
    json_object_from_text,
    message_text,
    skill_payload,
)
from app.services.skill_evaluation_llm_results import (
    normalize_case_results,
    scores_from_case_results,
    summary_payload,
)
from app.services.skill_evaluation_result_schema import normalize_skill_evaluation_result
from app.services.skill_evaluation_usage import LlmUsageCollector, resolve_model_pricing
from app.services.skill_evaluation_worker_types import (
    SkillEvaluationContext,
    SkillEvaluationResult,
)

type ModelBuilder = Callable[[AsyncSession], Awaitable[SkillBuilderChatModel]]

LLM_RUNNER_VERSION = "llm-1"
LLM_GRADER_PROMPT_VERSION = "llm-grader-1"


@dataclass(frozen=True, slots=True)
class LlmSkillEvaluationEvaluator:
    model_builder: ModelBuilder = build_skill_builder_chat_model
    runner_version: str = LLM_RUNNER_VERSION
    grader_prompt_version: str = LLM_GRADER_PROMPT_VERSION

    @classmethod
    def for_model(cls, model: BaseChatModel, *, model_name: str) -> LlmSkillEvaluationEvaluator:
        async def build_model(_db: AsyncSession) -> SkillBuilderChatModel:
            return SkillBuilderChatModel(model=model, model_name=model_name)

        return cls(model_builder=build_model)

    async def evaluate(
        self,
        db: AsyncSession,
        context: SkillEvaluationContext,
    ) -> SkillEvaluationResult:
        await context.cancellation.raise_if_cancelled(
            EvalCancellationCheckpoint(EvalCancellationPhase.START)
        )
        if not has_execution_cases(context.evals):
            await run_eval_runtime_policy_probe(context.runtime_context)
        _, execution_results = await deterministic_with_skill_results(
            context.evals,
            context.cancellation,
            context.runtime_context,
        )
        await context.cancellation.raise_if_cancelled(
            EvalCancellationCheckpoint(EvalCancellationPhase.GRADING)
        )
        built_model = await self.model_builder(db)
        usage_collector = LlmUsageCollector()
        response = await built_model.model.ainvoke(
            [
                SystemMessage(content=GRADER_SYSTEM_PROMPT),
                HumanMessage(
                    content=json.dumps(
                        {
                            "skill": skill_payload(context),
                            "evals": list(context.evals),
                            "execution_results": execution_results,
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
        )
        usage_collector.add_response(response)
        payload = json_object_from_text(message_text(response))
        case_results = normalize_case_results(
            evals=context.evals,
            payload=payload,
            execution_results=execution_results,
        )
        await context.cancellation.raise_if_cancelled(
            EvalCancellationCheckpoint(EvalCancellationPhase.AGGREGATION)
        )
        summary = summary_payload(
            evals=context.evals,
            case_results=case_results,
            payload=payload,
            runner_version=self.runner_version,
        )
        benchmark = aggregate_benchmark(
            with_skill=scores_from_case_results(case_results, baseline=False),
            without_skill=scores_from_case_results(case_results, baseline=True),
        )
        summary, benchmark, case_results = normalize_skill_evaluation_result(
            evals=context.evals,
            raw_case_results=case_results,
            raw_summary=summary,
            raw_benchmark=benchmark,
        )
        pricing = await resolve_model_pricing(db, built_model.model_name)
        return SkillEvaluationResult(
            summary=summary,
            benchmark=benchmark,
            case_results=case_results,
            runner_model=built_model.model_name,
            runner_version=self.runner_version,
            grader_prompt_version=self.grader_prompt_version,
            usage=usage_collector.rollup(pricing),
        )
