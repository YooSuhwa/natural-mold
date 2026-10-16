"""Lease-support policy for prepared-run inputs (parent Task 10, I16-I20 foundation).

The prepared adapter converts the legacy ``AgentConfig`` preparation into
``PreparationInputs``. Anything the lease cannot carry faithfully fails before
the materialization marker with a typed ``prepared_run_unsupported:<reason>``
instead of being dropped: non-standard runtime profiles, model fallback chains,
providers without a lease-owned single-send adapter, a missing LLM key,
configured (non-deepagents-builtin) middleware, provider auto-middleware (e.g.
OpenAI moderation performs its own unleased send), legacy-policy skills (they
need ``execute_in_skill``), memory write tools (live control-plane writes,
checked for the root and every child), ``recursion_limit`` (the frozen lease
config does not carry it) and ``agent_memory_file_write``: the legacy policy
grants the agent write on the shared ``/agents/<id>/AGENTS.md``; the lease
only serves it as a read-only snapshot (a writable run-root route would have
to be a prefix route over all of ``/agents/<id>/`` and would silently stop
persisting memory edits), so that shape fails typed instead.

Substitution (not degradation): the legacy default reliability middleware
``ModelRetryMiddleware`` is not installed; lease-owned models retry inside
``LeaseSendGuard`` with a fresh permit per attempt
(``lease_provider_retry_policy``). ``EmptyContentRetryMiddleware`` is kept: it
re-calls the model handler, so each retry is an ordinary permitted send.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.agent_runtime.lease_model_factory import SUPPORTED_PROVIDERS, LeaseModelSpec
from app.agent_runtime.runtime_config import AgentConfig

UNSUPPORTED = "prepared_run_unsupported"


class PreparedRunUnsupportedError(RuntimeError):
    """The run needs something a prepared lease cannot support; fail pre-marker."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        self.code = f"{UNSUPPORTED}:{reason}"
        super().__init__(self.code)


def unsupported(reason: str) -> PreparedRunUnsupportedError:
    return PreparedRunUnsupportedError(reason)


def lease_model_spec(
    *,
    provider: str,
    model_name: str,
    api_key: str | None,
    base_url: str | None,
    model_params: Mapping[str, Any] | None,
    context_window: int | None,
    subject: str = "",
) -> LeaseModelSpec:
    """Return a fresh lease model spec; never touches the shared model cache."""

    if provider not in SUPPORTED_PROVIDERS:
        raise unsupported(f"{subject}provider:{provider}")
    if not api_key:
        raise unsupported(f"{subject}llm_credential_missing")
    params = dict(model_params or {})
    if "recursion_limit" in params:
        raise unsupported(f"{subject}recursion_limit")
    if context_window and "context_window" not in params:
        params["context_window"] = context_window
    return LeaseModelSpec(
        provider=provider,
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        params=params,
    )


def configured_middleware_types(
    middleware_configs: Iterable[Mapping[str, Any]] | None,
    builtin_types: frozenset[str],
) -> list[str]:
    """Configured middleware types the lease would have to install (none allowed)."""

    return [
        str(config.get("type"))
        for config in middleware_configs or ()
        if config.get("type") not in builtin_types
    ]


def require_lease_supported(
    cfg: AgentConfig,
    *,
    builtin_types: frozenset[str],
    provider_middleware: Sequence[Any],
    memory_write_policy: str,
    subject: str = "",
) -> None:
    """Raise ``PreparedRunUnsupportedError`` for any shape the lease cannot carry."""

    if cfg.runtime_profile != "standard":
        raise unsupported(f"{subject}runtime_profile:{cfg.runtime_profile}")
    if cfg.model_fallback_chain:
        raise unsupported(f"{subject}model_fallback")
    configured = configured_middleware_types(cfg.middleware_configs, builtin_types)
    if configured:
        raise unsupported(f"{subject}middleware:{configured[0]}")
    if provider_middleware:
        raise unsupported(f"{subject}provider_middleware:{cfg.provider}")
    stored = cfg.runtime_policy.source == "stored"
    if cfg.agent_skills and not stored:
        raise unsupported(f"{subject}skill_execution")
    if cfg.agent_id and not stored:
        raise unsupported(f"{subject}agent_memory_file_write")
    if cfg.user_id is not None and memory_write_policy != "off":
        raise unsupported(f"{subject}memory_tools")
