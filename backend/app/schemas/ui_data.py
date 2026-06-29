"""Generative UI data-event contract (chat-generative-ui-dev-plan §5.1).

A ``moldy.ui_data`` custom SSE event carries a typed ``{type, props}`` payload
that the frontend renders as a React component (allowlist registry + Zod). This
is a side-channel beside the message content (FILE_EVENT/artifact precedent), so
it never touches the LangChain message / deepagents conversion.

Phase 1 ships only the ``demo_note`` type to prove the pipeline end-to-end. Real
component types (``data_table``/``chart``/``stats``/``terminal``) extend
``UIDataType`` in Phase 2.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

# Phase 2 extends: "data_table" | "chart" | "stats" | "terminal"
UIDataType = Literal["demo_note"]


class UIDataEvent(BaseModel):
    schema_version: Literal[1] = 1
    type: UIDataType
    # Attach target. Same rule as artifacts: exact assistant-message match, else
    # last-assistant fallback (set at the emit site / resolved on the frontend).
    message_id: str | None = None
    run_id: str | None = None
    tool_call_id: str | None = None
    # Per-type props. Validation lives on the frontend (Zod) + this server-side
    # ``type`` Literal guard; Phase 2 may add per-type Pydantic models.
    props: dict[str, Any]
