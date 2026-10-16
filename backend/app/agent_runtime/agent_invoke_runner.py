"""Non-streaming (trigger) agent invoke runner.

Collaborators patched on ``agent_stream_runner`` are resolved at call time.
"""

from __future__ import annotations

import time
import uuid as _uuid
from collections.abc import Sequence
from contextlib import nullcontext
from typing import Any

from app.agent_runtime.agent_stream_facade_bindings import stream_attr
from app.agent_runtime.message_utils import RuntimeMessage
from app.agent_runtime.runtime_config import AgentConfig
from app.hooks import HookResult


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

    langfuse_ctx = stream_attr("build_langfuse_run_context")(
        cfg,
        run_id=effective_run_id,
        source=moldy_source,
    )
    config = langfuse_ctx.configure_config(config)

    ctx = stream_attr("_hook_ctx_for_agent")(cfg)
    if ctx is not None:
        await stream_attr("hooks").run_pre(ctx)
    started = time.monotonic()

    activate = getattr(langfuse_ctx, "activate", None)
    activation: Any = (
        activate(input_payload={"messages": lc_messages}, output_payload=None)
        if callable(activate)
        else nullcontext()
    )
    try:
        with activation:
            result = await agent.ainvoke({"messages": lc_messages}, config=config)
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
            "messages", projected_content, secret_values=cfg.secret_values
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
    return text
