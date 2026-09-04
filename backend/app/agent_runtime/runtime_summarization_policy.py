"""Hydrate stored runtime summarization policy into Deep Agents middleware."""

from __future__ import annotations

from typing import assert_never

from deepagents.backends import BackendProtocol
from deepagents.middleware.summarization import (
    SummarizationMiddleware,
    compute_summarization_defaults,
)
from langchain_core.language_models import BaseChatModel

from app.agent_runtime.runtime_policy import (
    ResolvedRuntimePolicy,
    SummarizationAutoPolicyV1,
    SummarizationPresetPolicyV1,
)


class RuntimeSummarizationPolicyError(Exception):
    """The trusted policy cannot be hydrated for the effective model."""

    code = "RUNTIME_SUMMARIZATION_POLICY_INVALID"

    def __str__(self) -> str:
        return self.code


def create_stored_summarization_middleware(
    policy: ResolvedRuntimePolicy,
    model: BaseChatModel,
    backend: BackendProtocol,
) -> SummarizationMiddleware:
    """Create the sole summarizer authorized by a validated stored policy."""

    if policy.source != "stored":
        raise RuntimeSummarizationPolicyError

    match policy.effective.summarization:
        case SummarizationAutoPolicyV1():
            # The upstream mapping is the complete 0.7.11 contract. Passing it
            # through unchanged also leaves omitted constructor defaults under
            # Deep Agents' ownership.
            defaults = compute_summarization_defaults(model)
            return SummarizationMiddleware(
                model=model,
                backend=backend,
                trim_tokens_to_summarize=None,
                **defaults,
            )
        case SummarizationPresetPolicyV1(preset="balanced_context_v1"):
            profile = getattr(model, "profile", None)
            max_input_tokens = (
                profile.get("max_input_tokens") if isinstance(profile, dict) else None
            )
            if type(max_input_tokens) is not int or max_input_tokens <= 0:
                raise RuntimeSummarizationPolicyError
            return SummarizationMiddleware(
                model=model,
                backend=backend,
                trigger=("fraction", 0.80),
                keep=("fraction", 0.15),
                trim_tokens_to_summarize=None,
                truncate_args_settings={
                    "trigger": ("fraction", 0.70),
                    "keep": ("fraction", 0.10),
                    "max_length": 2000,
                    "truncation_text": "...(truncated)",
                },
            )
        case unreachable:
            assert_never(unreachable)
