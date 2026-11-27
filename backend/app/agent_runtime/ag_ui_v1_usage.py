"""Attribute AG-UI terminal usage to model calls observed in this physical run."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ag_ui.core import TokenUsage
from ag_ui.core.token_usage import aggregate_token_usage, token_usage_from_langchain_metadata
from pydantic import ValidationError

from app.agent_runtime.protocol_usage_normalization import text_value, usage_mapping

_MAX_COUNT = (1 << 53) - 1
_MODEL_LABEL = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:/@-]{0,199}")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _label(value: Any) -> str | None:
    if isinstance(value, str) and _MODEL_LABEL.fullmatch(value) and "://" not in value:
        return value
    return None


def _count(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= _MAX_COUNT else None


@dataclass
class _Call:
    source_id: str | None = None
    provider: str | None = None
    model: str | None = None
    counts: dict[str, int] = field(default_factory=dict)

    def record(self, name: str, value: int | None) -> None:
        if value is not None:
            self.counts[name] = max(self.counts.get(name, 0), value)

    def sample(self, usage: TokenUsage) -> None:
        self.record("input_tokens", usage.input_tokens)
        self.record("output_tokens", usage.output_tokens)
        self.record("reasoning_tokens", usage.reasoning_tokens)
        self.record("cached_input_tokens", usage.cached_input_tokens)
        self.record("cache_write_input_tokens", usage.cache_write_input_tokens)

    def entry(self) -> TokenUsage | None:
        counts = self.counts
        input_tokens = counts.get("input_tokens")
        output_tokens = counts.get("output_tokens")
        if input_tokens is None or output_tokens is None:
            return None
        if (
            counts.get("cached_input_tokens", 0) + counts.get("cache_write_input_tokens", 0)
            > input_tokens
        ):
            return None
        if counts.get("reasoning_tokens", 0) > output_tokens:
            return None
        return TokenUsage(
            provider=self.provider,
            model=self.model,
            total_tokens=input_tokens + output_tokens,
            **counts,
        )


def _message_sample(data: Mapping[str, Any], metadata: Mapping[str, Any]) -> TokenUsage | None:
    # A provider can leave zero placeholders in the chunk and report actual
    # totals in its sibling stream metadata. A usable direct snapshot wins.
    zero_sample = None
    for message in (data, metadata):
        usage = usage_mapping(message)
        if not usage:
            continue
        prompt_details = _mapping(usage.get("prompt_tokens_details"))
        completion_details = _mapping(usage.get("completion_tokens_details"))
        sample = token_usage_from_langchain_metadata(
            {
                "input_tokens": usage.get(
                    "input_tokens",
                    usage.get(
                        "prompt_tokens", usage.get("tokens_in", usage.get("total_input_tokens"))
                    ),
                ),
                "output_tokens": usage.get(
                    "output_tokens",
                    usage.get(
                        "completion_tokens",
                        usage.get("tokens_out", usage.get("total_output_tokens")),
                    ),
                ),
                "input_token_details": usage.get("input_token_details")
                or {
                    "cache_read": prompt_details.get(
                        "cached_tokens", usage.get("cache_read_tokens")
                    ),
                    "cache_creation": usage.get("cache_creation_tokens"),
                },
                "output_token_details": usage.get("output_token_details")
                or {
                    "reasoning": completion_details.get("reasoning_tokens"),
                },
            }
        )
        if sample is None or sample.input_tokens is None or sample.output_tokens is None:
            continue
        if sample.input_tokens or sample.output_tokens:
            return sample
        zero_sample = sample
    return zero_sample


class RunUsage:
    """One collector per physical run. Only current model messages establish ownership."""

    def __init__(self) -> None:
        self._calls: dict[tuple[str | None, str], _Call] = {}
        self._source_ids: dict[tuple[str | None, str], str] = {}
        # The journal derives a second usage record with an exact parent event ID.
        # In v3 a message-finish has usage but no assistant_msg_id.
        self._derived: dict[tuple[str | None, str], tuple[str, TokenUsage | None]] = {}
        self._uncertain = False

    def observe_message(
        self,
        data: Mapping[str, Any],
        *,
        subagent_run_id: str | None,
        public_message_id: str,
        event_id: str | None = None,
    ) -> None:
        """Register a produced assistant message. Never call this for checkpoint values."""
        key = (subagent_run_id, public_message_id)
        call = self._calls.setdefault(key, _Call())
        source = text_value(data.get("id") or data.get("message_id"))
        if source:
            known_public = self._source_ids.setdefault((subagent_run_id, source), public_message_id)
            if known_public != public_message_id or call.source_id not in (None, source):
                self._uncertain = True
            call.source_id = source
        metadata = _mapping(data.get("metadata"))
        response = _mapping(data.get("response_metadata"))
        call.provider = (
            _label(response.get("model_provider") or metadata.get("model_provider"))
            or _label(metadata.get("provider"))
            or call.provider
            or _label(metadata.get("ls_provider"))
        )
        call.model = (
            _label(
                response.get("model_name") or response.get("model") or metadata.get("model_name")
            )
            or _label(metadata.get("model"))
            or call.model
            or _label(metadata.get("ls_model_name"))
        )
        sample = _message_sample(data, metadata)
        if sample is not None:
            call.sample(sample)
        if event_id and (usage_mapping(data) or usage_mapping(metadata)):
            self._derived[subagent_run_id, f"{event_id}:usage"] = (public_message_id, sample)

    def observe_usage(
        self,
        payload: Mapping[str, Any],
        *,
        subagent_run_id: str | None,
        public_message_id: str | None,
        event_id: str | None = None,
    ) -> str | None:
        """Record attributable usage and return its verified public assistant message ID."""
        source = text_value(payload.get("assistant_msg_id"))
        input_tokens = _count(payload.get("prompt_tokens"))
        output_tokens = _count(payload.get("completion_tokens"))
        receipt = self._derived.get((subagent_run_id, event_id)) if event_id else None
        if receipt is not None:
            parent, sample = receipt
            if (
                public_message_id not in (None, parent)
                or (
                    source is not None and self._source_ids.get((subagent_run_id, source)) != parent
                )
                or sample is None
                or (input_tokens, output_tokens) != (sample.input_tokens, sample.output_tokens)
            ):
                self._uncertain = True
                return None
            # The measured parent already contains the counts for this canonical copy.
            return parent
        if source is None:
            self._uncertain = True
            return None
        known_public = self._source_ids.get((subagent_run_id, source))
        if public_message_id is None and known_public is None:
            # A values event can replay a previous turn's usage under today's run ID.
            return None
        if public_message_id != known_public or known_public is None:
            self._uncertain = True
            return None
        call = self._calls[(subagent_run_id, known_public)]
        if input_tokens is None or output_tokens is None:
            self._uncertain = True
            return None
        call.record("input_tokens", input_tokens)
        call.record("output_tokens", output_tokens)
        # The journal normalizer fills unreported cache counts with zero. Only
        # a nonzero canonical count or an explicit provider detail is evidence.
        call.record("cached_input_tokens", _count(payload.get("cache_read_tokens")) or None)
        call.record(
            "cache_write_input_tokens", _count(payload.get("cache_creation_tokens")) or None
        )
        return known_public

    def terminal_usage(self) -> list[TokenUsage] | None:
        if self._uncertain or not self._calls:
            return None
        entries: list[TokenUsage] = []
        try:
            for call in self._calls.values():
                entry = call.entry()
                if entry is None:
                    return None
                entries.append(entry)
            return aggregate_token_usage(entries)
        except ValidationError:
            # Valid per-call counts may still exceed JS safe integers when summed.
            return None
