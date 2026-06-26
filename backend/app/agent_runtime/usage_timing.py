"""Streaming timing metrics (TTFT / total / tok-s) co-located with usage.

These ride alongside the existing token/cost ``usage`` payload so the per-message
hover popover can show generation speed. Live-only: not persisted to the
checkpointer (timing is an ephemeral streaming metric — it disappears on reload,
which is acceptable; token/cost persist as before).
"""

from __future__ import annotations

import time


def compute_usage_timing(
    *,
    started_at: float,
    first_token_at: float | None,
    completion_tokens: int,
) -> dict[str, float]:
    """Return ``{ttft_ms?, generation_ms, tokens_per_second?}`` from monotonic marks.

    - ``started_at`` / ``first_token_at`` are ``time.monotonic()`` samples.
    - ``generation_ms`` is total wall time since stream start.
    - ``ttft_ms`` is time to the first content token (omitted if none yet).
    - ``tokens_per_second`` is output throughput over the generation phase
      (after the first token), omitted when no completion tokens.
    """
    now = time.monotonic()
    timing: dict[str, float] = {"generation_ms": round((now - started_at) * 1000, 1)}
    if first_token_at is not None:
        timing["ttft_ms"] = round((first_token_at - started_at) * 1000, 1)
    gen_seconds = now - (first_token_at if first_token_at is not None else started_at)
    if completion_tokens > 0 and gen_seconds > 0:
        timing["tokens_per_second"] = round(completion_tokens / gen_seconds, 1)
    return timing
