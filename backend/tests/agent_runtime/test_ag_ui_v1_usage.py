from __future__ import annotations

from typing import Any

import pytest
from ag_ui.core import TokenUsage

from app.agent_runtime.ag_ui_v1_usage import RunUsage
from app.agent_runtime.protocol_events import stored_protocol_event
from app.agent_runtime.protocol_usage import collect_protocol_usage_event


def record(
    collector: RunUsage, raw: str, public: str, *, child: str | None = None, **fields: Any
) -> None:
    collector.observe_message(
        {"type": "AIMessageChunk", "id": raw, **fields},
        subagent_run_id=child,
        public_message_id=public,
    )


def usage(
    collector: RunUsage,
    raw: str | None,
    public: str | None,
    prompt: int,
    completion: int,
    *,
    child: str | None = None,
    **fields: Any,
) -> str | None:
    return collector.observe_usage(
        {
            "assistant_msg_id": raw,
            "run_id": "physical-run",
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            **fields,
        },
        subagent_run_id=child,
        public_message_id=public,
    )


def test_cumulative_usage_groups_distinct_calls_and_models_and_discards_replayed_snapshots() -> (
    None
):
    tracker = RunUsage()
    metadata = {"ls_provider": "openai", "ls_model_name": "gpt-test"}
    record(tracker, "a", "a", metadata=metadata, content="PRIVATE", user_id="PRIVATE")
    usage(tracker, "a", "a", 7, 2)
    record(
        tracker,
        "a",
        "a",
        response_metadata={"model_provider": "openai", "model_name": "gpt-actual"},
        usage_metadata={
            "input_tokens": 10,
            "output_tokens": 4,
            "total_tokens": 99,
            "input_token_details": {"cache_read": 6, "cache_creation": 1},
            "output_token_details": {"reasoning": 2, "private": "PRIVATE"},
        },
    )
    usage(tracker, "a", "a", 10, 4)
    usage(tracker, "a", "a", 7, 2)
    record(
        tracker,
        "a",
        "child-message",
        child="child",
        metadata=metadata,
        usage_metadata={"input_tokens": 7, "output_tokens": 2},
    )
    # The journal deduplicates equal raw assistant IDs and counts globally.
    # The child's current provider observation still contributes its own call.
    record(
        tracker,
        "b",
        "b",
        response_metadata={"model_provider": "openai", "model_name": "gpt-actual"},
    )
    usage(tracker, "b", "b", 5, 2)
    expected = [
        TokenUsage(
            provider="openai",
            model="gpt-actual",
            input_tokens=15,
            output_tokens=6,
            total_tokens=21,
            cached_input_tokens=6,
            cache_write_input_tokens=1,
            reasoning_tokens=2,
        ),
        TokenUsage(
            provider="openai", model="gpt-test", input_tokens=7, output_tokens=2, total_tokens=9
        ),
    ]
    assert tracker.terminal_usage() == expected
    assert tracker.terminal_usage() == expected


def test_current_run_id_on_historical_activity_does_not_charge_prior_turn() -> None:
    tracker = RunUsage()
    assert usage(tracker, "prior-turn", None, 100, 100) is None
    assert tracker.terminal_usage() is None
    record(tracker, "reply", "public-reply")
    assert usage(tracker, "reply", "public-reply", 8, 4) == "public-reply"
    assert usage(tracker, "prior-turn", None, 100, 100) is None
    usage(tracker, "reply", None, 100, 100, child="historical-child")
    assert tracker.terminal_usage() == [
        TokenUsage(input_tokens=8, output_tokens=4, total_tokens=12)
    ]


def test_real_journal_defaults_are_absent_but_reported_provider_zero_survives() -> None:
    event = stored_protocol_event(
        run_id="physical-run",
        thread_id="thread",
        seq=1,
        method="messages",
        data={
            "id": "message",
            "type": "ai",
            "usage_metadata": {"input_tokens": 0, "output_tokens": 0},
            "metadata": {
                "ls_provider": "openai",
                "ls_model_name": "gpt-test",
                "usage_metadata": {
                    "input_tokens": 12,
                    "output_tokens": 4,
                    "input_token_details": {"cache_read": 0},
                    "output_token_details": {"reasoning": 0},
                },
            },
        },
    )
    custom, _ = collect_protocol_usage_event(
        event,
        next_seq=1,
        seen_keys=set(),
        usage_sink=None,
        cost_per_input_token=None,
        cost_per_output_token=None,
    )
    assert custom is not None
    payload = custom["data"]["payload"]
    assert payload["cache_creation_tokens"] == 0 and payload["cache_read_tokens"] == 0
    tracker = RunUsage()
    tracker.observe_message(event["data"], subagent_run_id=None, public_message_id="message")
    tracker.observe_usage(payload, subagent_run_id=None, public_message_id="message")
    result = tracker.terminal_usage()
    assert result and result[0].model_dump(by_alias=True, exclude_none=True) == {
        "provider": "openai",
        "model": "gpt-test",
        "inputTokens": 12,
        "outputTokens": 4,
        "totalTokens": 16,
        "cachedInputTokens": 0,
        "reasoningTokens": 0,
    }


def test_provider_response_metadata_includes_measured_cache_and_reasoning() -> None:
    tracker = RunUsage()
    record(
        tracker,
        "current",
        "current",
        response_metadata={
            "model": "org/model",
            "token_usage": {
                "prompt_tokens": 18,
                "completion_tokens": 7,
                "prompt_tokens_details": {"cached_tokens": 9},
                "completion_tokens_details": {"reasoning_tokens": 3},
            },
        },
    )
    assert tracker.terminal_usage() == [
        TokenUsage(
            model="org/model",
            input_tokens=18,
            output_tokens=7,
            total_tokens=25,
            cached_input_tokens=9,
            reasoning_tokens=3,
        )
    ]


@pytest.mark.parametrize(
    ("raw", "public"), [(None, None), (None, "current"), ("current", None), ("old", "current")]
)
def test_unattributable_sample_omits_terminal_aggregate(
    raw: str | None, public: str | None
) -> None:
    tracker = RunUsage()
    record(tracker, "current", "current", usage_metadata={"input_tokens": 8, "output_tokens": 1})
    usage(tracker, raw, public, 6, 2)
    assert tracker.terminal_usage() is None


def test_unreported_current_call_does_not_silently_reduce_terminal_total() -> None:
    tracker = RunUsage()
    record(tracker, "known", "known", usage_metadata={"input_tokens": 5, "output_tokens": 1})
    record(tracker, "unknown", "unknown", child="child")
    assert tracker.terminal_usage() is None


@pytest.mark.parametrize("malformed", [True, -1, 1.5, "4", 2**53, float("nan")])
def test_invalid_canonical_pair_omits_usage_without_breaking_terminal(malformed: Any) -> None:
    tracker = RunUsage()
    record(tracker, "a", "a")
    usage(tracker, "a", "a", malformed, 1)
    assert tracker.terminal_usage() is None


def test_aggregate_count_overflow_never_breaks_the_terminal_event() -> None:
    tracker = RunUsage()
    record(tracker, "a", "a", usage_metadata={"input_tokens": 2**52, "output_tokens": 0})
    record(tracker, "b", "b", usage_metadata={"input_tokens": 2**52, "output_tokens": 0})
    assert tracker.terminal_usage() is None


def test_inconsistent_detail_counts_are_never_added_to_provider_totals() -> None:
    tracker = RunUsage()
    record(tracker, "a", "a")
    usage(tracker, "a", "a", 5, 2, cache_creation_tokens=3, cache_read_tokens=3)
    assert tracker.terminal_usage() is None


def test_v3_finish_and_its_derived_idless_journal_entry_are_counted_once() -> None:
    from langchain_core.language_models._compat_bridge import chunks_to_events
    from langchain_core.messages import AIMessageChunk
    from langchain_core.outputs import ChatGenerationChunk

    tracker = RunUsage()
    chunk = AIMessageChunk(
        id="private-source",
        content="",
        response_metadata={"model_provider": "anthropic", "model_name": "claude-test"},
        usage_metadata={
            "input_tokens": 5,
            "output_tokens": 2,
            "total_tokens": 7,
            "input_token_details": {"cache_read": 1},
        },
    )
    for seq, data in enumerate(chunks_to_events(iter([ChatGenerationChunk(message=chunk)])), 1):
        event = stored_protocol_event(
            run_id="physical-run",
            thread_id="thread",
            seq=seq,
            method="messages",
            data=data,
        )
        if data["event"] not in ("message-start", "message-finish"):
            continue
        tracker.observe_message(
            event["data"],
            subagent_run_id="child",
            public_message_id="public-call",
            event_id=event["id"],
        )
        companion, _ = collect_protocol_usage_event(
            event,
            next_seq=seq,
            seen_keys=set(),
            usage_sink=None,
            cost_per_input_token=None,
            cost_per_output_token=None,
        )
        if data["event"] == "message-finish":
            assert companion is not None
            payload = companion["data"]["payload"]
            assert "assistant_msg_id" not in payload
            parent = tracker.observe_usage(
                payload, subagent_run_id="child", public_message_id=None, event_id=companion["id"]
            )
            assert parent == "public-call"
    assert tracker.terminal_usage() == [
        TokenUsage(
            provider="anthropic",
            model="claude-test",
            input_tokens=5,
            output_tokens=2,
            total_tokens=7,
            cached_input_tokens=1,
        )
    ]


@pytest.mark.parametrize("forged", ["event-id", "totals", "source", "scope", "parent"])
def test_anonymous_receipt_requires_exact_event_id_owner_parent_and_measurement(
    forged: str,
) -> None:
    tracker = RunUsage()
    tracker.observe_message(
        {"event": "message-finish", "usage": {"input_tokens": 8, "output_tokens": 3}},
        subagent_run_id=None,
        public_message_id="public-call",
        event_id="finish",
    )
    parent = tracker.observe_usage(
        {
            "assistant_msg_id": "unknown" if forged == "source" else None,
            "prompt_tokens": 77 if forged == "totals" else 8,
            "completion_tokens": 3,
        },
        subagent_run_id="child" if forged == "scope" else None,
        public_message_id="other" if forged == "parent" else None,
        event_id="unrelated:usage" if forged == "event-id" else "finish:usage",
    )
    assert parent is None
    assert tracker.terminal_usage() == [
        TokenUsage(input_tokens=8, output_tokens=3, total_tokens=11)
    ]


def test_coercion_by_legacy_derived_event_does_not_turn_invalid_raw_counts_into_usage() -> None:
    tracker = RunUsage()
    event = stored_protocol_event(
        run_id="physical-run",
        thread_id="thread",
        seq=1,
        method="messages",
        data={
            "type": "AIMessageChunk",
            "id": "call",
            "usage_metadata": {"input_tokens": True, "output_tokens": "23"},
        },
    )
    tracker.observe_message(
        event["data"], subagent_run_id=None, public_message_id="call", event_id=event["id"]
    )
    companion, _ = collect_protocol_usage_event(
        event,
        next_seq=1,
        seen_keys=set(),
        usage_sink=None,
        cost_per_input_token=None,
        cost_per_output_token=None,
    )
    assert companion is not None
    payload = companion["data"]["payload"]
    assert (payload["prompt_tokens"], payload["completion_tokens"]) == (1, 23)
    tracker.observe_usage(
        payload, subagent_run_id=None, public_message_id="call", event_id=companion["id"]
    )
    assert tracker.terminal_usage() is None


@pytest.mark.parametrize("compact", [False, True])
def test_values_only_receipt_from_current_assistant_survives_persistence(compact: bool) -> None:
    from app.agent_runtime.protocol_persistence import persistable_protocol_event

    tracker = RunUsage()
    record(tracker, "current", "public", child="child")
    event = stored_protocol_event(
        run_id="physical-run",
        thread_id="thread",
        seq=4,
        method="values",
        namespace=["child"],
        data={
            "messages": [
                {
                    "id": "previous",
                    "type": "ai",
                    "usage_metadata": {"input_tokens": 32, "output_tokens": 14},
                },
                {
                    "id": "current",
                    "type": "ai",
                    "usage_metadata": {
                        "input_tokens": 7,
                        "output_tokens": 2,
                        "input_token_details": {"cache_read": 3},
                    },
                },
                {"id": "tool-output", "type": "tool", "content": "result"},
            ]
        },
    )
    companion, _ = collect_protocol_usage_event(
        event,
        next_seq=4,
        seen_keys=set(),
        usage_sink=None,
        cost_per_input_token=None,
        cost_per_output_token=None,
    )
    assert companion is not None
    data = persistable_protocol_event(event)["data"] if compact else event["data"]
    tracker.observe_values_parent(data, event_id=event["id"], subagent_run_id="child")
    payload = companion["data"]["payload"]
    assert (
        tracker.observe_usage(
            payload, event_id=companion["id"], subagent_run_id="child", public_message_id="public"
        )
        == "public"
    )
    assert tracker.terminal_usage() == [
        TokenUsage(input_tokens=7, output_tokens=2, total_tokens=9, cached_input_tokens=3)
    ]


@pytest.mark.parametrize(
    "orphan",
    [
        "arbitrary-id",
        "companion-of-unseen-checkpoint",
        "prior",
        "hidden-scope",
        "repeated-current-ref",
        "multiple-current-refs",
        "wrong-current-ref",
        "wrong-counts",
        "wrong-cache",
        "wrong-compact-cache",
    ],
)
def test_unproven_usage_activity_cannot_displace_observed_model_usage(orphan: str) -> None:
    tracker = RunUsage()
    measured = {"input_tokens": 4, "output_tokens": 2, "input_token_details": {"cache_read": 1}}
    tracker.observe_message(
        {"id": "current", "usage_metadata": measured},
        event_id="measured",
        subagent_run_id=None,
        public_message_id="target",
    )
    ref = {"id": "current", "type": "ai"}
    refs = [ref]
    if orphan == "repeated-current-ref":
        refs.append(ref)
    if orphan == "multiple-current-refs":
        refs.append({"id": "other", "type": "ai"})
        record(tracker, "other", "other", usage_metadata=measured)
    if orphan == "prior":
        refs = [{"id": "previous", "type": "ai"}]
    tracker.observe_values_parent({"messages": refs}, event_id="checkpoint", subagent_run_id=None)
    metrics = {
        "assistant_msg_id": "previous" if orphan in ("prior", "wrong-current-ref") else "current",
        "prompt_tokens": 66 if orphan == "wrong-counts" else 4,
        "completion_tokens": 2,
        "cache_read_tokens": 3 if orphan in ("wrong-cache", "wrong-compact-cache") else 1,
    }
    event_id = {
        "arbitrary-id": "unrelated",
        "companion-of-unseen-checkpoint": "unseen:usage",
        "wrong-cache": "measured:usage",
    }.get(orphan, "checkpoint:usage")
    parent = tracker.observe_usage(
        metrics,
        event_id=event_id,
        subagent_run_id="hidden" if orphan == "hidden-scope" else None,
        public_message_id="target",
    )
    assert parent is None
    n = 2 if orphan == "multiple-current-refs" else 1
    assert tracker.terminal_usage() == [
        TokenUsage(
            input_tokens=4 * n, output_tokens=2 * n, total_tokens=6 * n, cached_input_tokens=n
        )
    ]
