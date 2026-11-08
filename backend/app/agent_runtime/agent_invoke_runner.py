"""Non-streaming (trigger) agent invoke runner.

Collaborators patched on ``agent_stream_runner`` are resolved at call time.
"""

from __future__ import annotations

import time
import uuid as _uuid
from collections.abc import Collection, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from app.agent_runtime.agent_stream_facade_bindings import stream_attr
from app.agent_runtime.message_utils import RuntimeMessage
from app.agent_runtime.prepared_run_dispatch import (
    USE_LEASE_MESSAGES,
    PreparedRunHandle,
    lease_graph_input,
    lease_run_config,
    prepared_dispatch,
)
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.structured_output import (
    StructuredOutcome,
    StructuredOutputExhausted,
    structured_outcome,
)
from app.hooks import HookResult


@dataclass(frozen=True, slots=True)
class StructuredInvokeResult:
    """A schema-bound invoke (workflow W0): the redacted text and the checked JSON."""

    text: str
    outcome: StructuredOutcome


async def execute_agent_invoke(
    cfg: AgentConfig,
    messages_history: Sequence[RuntimeMessage],
    *,
    run_id: str | None = None,
    moldy_source: str = "trigger",
) -> str:
    """비스트리밍 실행 (트리거용). 최종 응답 텍스트만 반환."""
    # ADR-021 H1 — install the run-scoped secret set so the lazy skill-credential
    # union in ``_prepare_runtime_components`` works and any redaction during the
    # invoke (langfuse mask, persistence) sees the run's real secrets.
    secret_token = stream_attr("set_run_secrets")(cfg.secret_values)
    try:
        return await stream_attr("_execute_agent_invoke_inner")(
            cfg,
            messages_history,
            run_id=run_id,
            moldy_source=moldy_source,
        )
    finally:
        stream_attr("reset_run_secrets")(secret_token)


async def _execute_agent_invoke_inner(
    cfg: AgentConfig,
    messages_history: Sequence[RuntimeMessage],
    *,
    run_id: str | None = None,
    moldy_source: str = "trigger",
) -> str:
    effective_run_id = run_id or str(_uuid.uuid4())
    agent, lc_messages, config = await stream_attr("_prepare_agent")(
        cfg,
        messages_history=messages_history,
        is_trigger_mode=True,
        run_id=effective_run_id,
    )
    text, _ = await _invoke_built_agent(
        cfg,
        agent,
        {"messages": lc_messages},
        config,
        run_id=effective_run_id,
        moldy_source=moldy_source,
    )
    return text


async def execute_agent_invoke_structured(
    cfg: AgentConfig,
    messages_history: Sequence[RuntimeMessage],
    *,
    run_id: str | None = None,
    moldy_source: str = "workflow",
) -> StructuredInvokeResult:
    """Invoke a run that declares ``cfg.output_schema`` and check its answer.

    A second schema miss (after the one retry) is ``schema_invalid``, not an error: the
    caller decides what a failed step does. The value is redacted like the text.
    """
    schema = cfg.output_schema
    if schema is None:
        raise ValueError("execute_agent_invoke_structured needs cfg.output_schema")
    secret_token = stream_attr("set_run_secrets")(cfg.secret_values)
    try:
        effective_run_id = run_id or str(_uuid.uuid4())
        agent, lc_messages, config = await stream_attr("_prepare_agent")(
            cfg,
            messages_history=messages_history,
            is_trigger_mode=True,
            run_id=effective_run_id,
        )
        try:
            text, result = await _invoke_built_agent(
                cfg,
                agent,
                {"messages": lc_messages},
                config,
                run_id=effective_run_id,
                moldy_source=moldy_source,
            )
        except StructuredOutputExhausted:
            return StructuredInvokeResult("", StructuredOutcome("schema_invalid"))
        outcome = structured_outcome(result, schema)
        if outcome.value is not None:
            redacted = stream_attr("redact_protocol_data")(
                "messages", outcome.value, secret_values=cfg.secret_values
            )
            outcome = StructuredOutcome(outcome.status, redacted)
        return StructuredInvokeResult(text, outcome)
    finally:
        stream_attr("reset_run_secrets")(secret_token)


async def execute_prepared_agent_invoke(
    cfg: AgentConfig,
    handle: PreparedRunHandle,
    *,
    stream_input: Any = USE_LEASE_MESSAGES,
    moldy_source: str = "trigger",
    run_id: str | None = None,
) -> str:
    """Invoke the exact activated lease graph (Task 10 I20); never ``_prepare_agent``.

    ``cfg`` is Langfuse/cost metadata only (hooks skipped); ``prepared_dispatch``
    claims the lease once, installs its secret set, runs under
    ``post_marker_execution`` and terminalizes.
    """

    async with prepared_dispatch(handle) as lease:
        text, _ = await _invoke_built_agent(
            cfg,
            lease.runnable,
            lease_graph_input(lease, stream_input),
            lease_run_config(lease),
            run_id=run_id or str(lease.binding.run_id),
            moldy_source=moldy_source,
            secret_values=lease.secret_values,
            hooks_enabled=False,
        )
        return text


async def _invoke_built_agent(
    cfg: AgentConfig,
    agent: Any,
    graph_input: Any,
    config: dict[str, Any],
    *,
    run_id: str,
    moldy_source: str,
    secret_values: Collection[str] | None = None,
    hooks_enabled: bool = True,
) -> tuple[str, Any]:
    """The redacted final text and the graph's final state."""
    langfuse_ctx = stream_attr("build_langfuse_run_context")(
        cfg,
        run_id=run_id,
        source=moldy_source,
    )
    config = langfuse_ctx.configure_config(config)

    # Prepared leases skip the DB-writing hooks (outside the I14 write capability).
    ctx = stream_attr("_hook_ctx_for_agent")(cfg) if hooks_enabled else None
    if ctx is not None:
        await stream_attr("hooks").run_pre(ctx)
    started = time.monotonic()

    activate = getattr(langfuse_ctx, "activate", None)
    activation: Any = (
        activate(input_payload=graph_input, output_payload=None)
        if callable(activate)
        else nullcontext()
    )
    try:
        with activation:
            result = await agent.ainvoke(graph_input, config=config)
    except Exception as exc:
        if ctx is not None:
            await stream_attr("hooks").run_failure(ctx, exc)
        raise
    finally:
        langfuse_ctx.flush()

    messages = result.get("messages", [])
    text = ""
    if messages and hasattr(messages[-1], "content"):
        projected_content = stream_attr("project_offload_egress_data")(messages[-1].content)
        redacted_content = stream_attr("redact_protocol_data")(
            "messages",
            projected_content,
            secret_values=cfg.secret_values if secret_values is None else secret_values,
        )
        text = stream_attr("content_to_text")(redacted_content)

    if ctx is not None:
        await stream_attr("hooks").run_post(
            ctx,
            HookResult(
                duration_ms=int((time.monotonic() - started) * 1000),
                output=text[:200],
            ),
        )
    return text, result
