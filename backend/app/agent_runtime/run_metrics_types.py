"""Immutable run-metrics values shared by capture and persistence layers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

TerminalRunState = Literal["completed", "failed", "canceled", "interrupted", "stale"]
ActivityKind = Literal["model_usage", "tool_call", "subagent_call"]
type MessageIdentity = tuple[tuple[str, ...], str]


class RunActivityPayload(TypedDict):
    kind: ActivityKind
    namespace: list[str]
    call_id: str | None
    name: str | None
    elapsed_ms: float | None


class RunMetricsPayload(TypedDict):
    terminal_state: TerminalRunState | None
    elapsed_ms: float | None
    ttft_ms: float | None
    generation_ms: float | None
    tokens_per_second: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    cache_creation_tokens: int | None
    cache_read_tokens: int | None
    estimated_cost: float | None
    usage_complete: bool
    root_tool_calls: int | None
    descendant_tool_calls: int | None
    root_subagent_calls: int | None
    descendant_subagent_calls: int | None
    activity: list[RunActivityPayload]
    activity_truncated: bool


@dataclass(frozen=True, slots=True)
class RunMetricActivity:
    """One compact, display-safe activity captured from an actual protocol event."""

    kind: ActivityKind
    namespace: tuple[str, ...]
    call_id: str | None
    name: str | None
    elapsed_ms: float | None

    def to_persistence_payload(self) -> RunActivityPayload:
        return {
            "kind": self.kind,
            "namespace": list(self.namespace),
            "call_id": self.call_id,
            "name": self.name,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass(frozen=True, slots=True)
class RunMetricsSnapshot:
    """Immutable persistence-ready view of accumulated run measurements."""

    terminal_state: TerminalRunState | None
    elapsed_ms: float | None
    ttft_ms: float | None
    generation_ms: float | None
    tokens_per_second: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    cache_creation_tokens: int | None
    cache_read_tokens: int | None
    estimated_cost: float | None
    usage_complete: bool
    root_tool_calls: int | None
    descendant_tool_calls: int | None
    root_subagent_calls: int | None
    descendant_subagent_calls: int | None
    activity: tuple[RunMetricActivity, ...]
    activity_truncated: bool

    def to_persistence_payload(self) -> RunMetricsPayload:
        return {
            "terminal_state": self.terminal_state,
            "elapsed_ms": self.elapsed_ms,
            "ttft_ms": self.ttft_ms,
            "generation_ms": self.generation_ms,
            "tokens_per_second": self.tokens_per_second,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "estimated_cost": self.estimated_cost,
            "usage_complete": self.usage_complete,
            "root_tool_calls": self.root_tool_calls,
            "descendant_tool_calls": self.descendant_tool_calls,
            "root_subagent_calls": self.root_subagent_calls,
            "descendant_subagent_calls": self.descendant_subagent_calls,
            "activity": [item.to_persistence_payload() for item in self.activity],
            "activity_truncated": self.activity_truncated,
        }
