from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ag_ui.core import BaseEvent, EventType, TextMessageContentEvent, TextMessageEndEvent
from langchain_core.language_models._compat_bridge import message_to_events
from langchain_core.messages import AIMessage

from app.agent_runtime.ag_ui_v1_entities import PublicChatEntities
from app.agent_runtime.protocol_events import stored_protocol_event


def message(
    entities: PublicChatEntities, data: Any, *, owner: str | None = None, seq: int = 1
) -> list[BaseEvent]:
    return entities.project(
        stored_protocol_event(run_id="r", thread_id="t", seq=seq, method="messages", data=data),
        owner=owner,
    )


def text(events: Iterable[BaseEvent]) -> str:
    return "".join(evt.delta for evt in events if isinstance(evt, TextMessageContentEvent))


def test_official_langchain_block_finish_does_not_duplicate_text_or_tool_arguments() -> None:
    entities = PublicChatEntities("r", lambda _name, _id: True)
    original = AIMessage(
        id="provider-id",
        content="보고서를 확인합니다. ",
        tool_calls=[{"id": "call-a", "name": "find", "args": {"q": "서울"}, "type": "tool_call"}],
    )
    frames = [
        frame
        for i, source in enumerate(message_to_events(original))
        for frame in message(entities, source, seq=i)
    ]
    frames.extend(entities.close(None))

    assert text(frames) == original.content
    assert [e.type for e in frames].count(EventType.TEXT_MESSAGE_START) == 1
    assert [e.type for e in frames].count(EventType.TEXT_MESSAGE_END) == 1
    assert [e.type for e in frames].count(EventType.TOOL_CALL_END) == 1
    assert entities.current_message_id(None) == "provider-id"
    assert entities.tool_parent_message_id(None, "call-a") == "provider-id"


def test_legacy_repeated_full_message_and_finalized_chunks_only_emit_new_text() -> None:
    entities = PublicChatEntities("r", lambda _name, _id: True)
    chunk = {"id": "answer", "type": "AIMessageChunk", "content": " 좋"}
    frames = message(entities, [chunk, {"ls_provider": "fixture"}])
    frames += message(entities, {"id": "answer", "type": "AIMessageChunk", "content": "아요"})
    final = {"id": "answer", "type": "ai", "content": " 좋아요!"}
    frames += message(entities, final)
    frames += message(entities, final)

    assert text(frames) == " 좋아요!"
    assert frames[-1].type == EventType.TEXT_MESSAGE_END
    assert entities.close(None) == []


def test_private_content_and_non_assistant_streams_do_not_create_message_anchors() -> None:
    entities = PublicChatEntities("r", lambda _name, _id: False)
    raw = {"id": "private", "type": "ai", "content": [{"type": "reasoning", "text": "private"}]}
    assert message(entities, raw) == []
    assert entities.known_message_id(None, "private") is None
    for value in (
        {"event": "message-start", "id": "hidden", "role": "system"},
        {"event": "content-block-delta", "delta": {"type": "text-delta", "text": "private"}},
        {"event": "message-finish"},
        {"role": "human", "content": "private"},
    ):
        assert message(entities, value) == []
    frames = [
        e
        for source in message_to_events(AIMessage(id="real", content="공개"))
        for e in message(entities, source)
    ]
    assert text(frames) == "공개"
    assert entities.known_message_id(None, "hidden") is None


def test_interleaved_owners_have_separate_stable_ids_and_close_independently() -> None:
    def replay() -> tuple[list[BaseEvent], PublicChatEntities]:
        entities = PublicChatEntities("r", lambda _name, _id: True)
        frames = []
        for owner, delta in (("left", "1"), ("right", "x"), ("left", "2"), (None, "p")):
            frames += message(
                entities, {"id": "same", "type": "AIMessageChunk", "content": delta}, owner=owner
            )
        for owner in ("left", "right", None):
            (end,) = entities.close(owner)
            assert isinstance(end, TextMessageEndEvent) and end.subagent_run_id == owner
            frames.append(end)
        return frames, entities

    first, entities = replay()
    assert replay()[0] == first
    assert entities.known_message_id(None, "same") == "same"
    assert len({entities.known_message_id(owner, "same") for owner in ("left", "right", None)}) == 3
    assert (
        text(
            e
            for e in first
            if isinstance(e, TextMessageContentEvent) and e.subagent_run_id == "left"
        )
        == "12"
    )


def test_secret_split_across_fragments_is_never_emitted() -> None:
    entities = PublicChatEntities("r", lambda _name, _id: True, secret_values=["private-abc"])
    frames = [
        e
        for part in ("응답 ", "priv", "ate", "-abc", " 끝")
        for e in message(entities, {"type": "AIMessageChunk", "id": "m", "content": part})
    ] + entities.close(None)
    assert text(frames) == "응답 <redacted> 끝"


def test_source_event_is_stable_when_a_message_has_no_provider_id() -> None:
    def replay() -> tuple[str | None, str]:
        entities = PublicChatEntities("r", lambda _name, _id: True)
        first = message(entities, {"chunk": "시작"}, seq=5)
        following = message(entities, {"chunk": " 끝"}, seq=6)
        entities.close(None)
        return entities.current_message_id(None), text([*first, *following])

    identity, result = replay()
    assert identity and identity != "r"
    assert result == "시작 끝"
    assert replay() == (identity, result)


def test_second_response_without_provider_id_gets_its_own_anchor() -> None:
    entities = PublicChatEntities("r", lambda _name, _id: True)
    identities = []
    for seq in (1, 5):
        message(entities, {"event": "message-start", "role": "assistant"}, seq=seq)
        message(
            entities,
            {
                "event": "content-block-delta",
                "index": 0,
                "delta": {
                    "type": "text-delta",
                    "text": "답변",
                },
            },
            seq=seq + 1,
        )
        (end,) = message(entities, {"event": "message-finish"}, seq=seq + 2)
        assert isinstance(end, TextMessageEndEvent)
        identities.append(end.message_id)
    assert len(set(identities)) == 2


def test_usage_can_reference_observed_tool_only_messages_but_not_an_orphan_finish() -> None:
    entities = PublicChatEntities("r", lambda _name, _id: True)
    assert message(entities, {"event": "message-finish", "id": "orphan"}) == []
    assert entities.observed_message_id(None, "orphan") is None
    assert entities.current_observed_message_id(None) is None
    assert message(entities, {"event": "message-start", "role": "ai", "id": "issuer"}) == []
    assert entities.known_message_id(None, "issuer") is None
    assert entities.observed_message_id(None, "issuer") == "issuer"
    assert message(entities, {"event": "message-finish"}) == []
    assert entities.current_observed_message_id(None) == "issuer"
    assert message(entities, {"event": "message-start", "role": "user", "id": "u"}) == []
    assert entities.current_observed_message_id(None) is None


def test_non_assistant_deltas_cannot_be_relabelled_by_an_interleaved_assistant_start() -> None:
    entities = PublicChatEntities("r", lambda _name, _id: True)
    message(entities, {"event": "message-start", "role": "system", "id": "private"})
    message(entities, {"event": "message-start", "role": "assistant", "id": "answer"})
    delta = {"event": "content-block-delta", "delta": {"type": "text-delta", "text": "x"}}
    assert not message(entities, {"id": "private", **delta})
    assert not message(entities, {"id": "invalid", "event": {"text": "private"}})
    assert text(message(entities, {"id": "answer", **delta})) == "x"


def test_child_cause_requires_an_observed_and_still_visible_delegation_in_the_exact_owner() -> None:
    allowed = {"delegate", "query"}
    entities = PublicChatEntities("r", lambda _name, raw_id: raw_id in allowed)
    for raw_id, name, owner in (("delegate", "task", "parent"), ("query", "search", "parent")):
        message(
            entities,
            {"type": "ai", "id": "issuer", "tool_calls": [{"id": raw_id, "name": name}]},
            owner=owner,
        )
    assert entities.public_tool_id("parent", "query")
    assert entities.public_delegation_tool_id("parent", "query") is None
    assert entities.public_delegation_tool_id(None, "delegate") is None
    assert entities.public_delegation_tool_id("parent", "unseen") is None
    assert entities.public_delegation_tool_id("parent", "delegate") == entities.public_tool_id(
        "parent", "delegate"
    )
    allowed.remove("delegate")
    assert entities.public_delegation_tool_id("parent", "delegate") is None


def test_allowed_parent_call_id_does_not_grant_an_unrelated_nested_delegation() -> None:
    allowed_parents: set[str | None] = {None}
    entities = PublicChatEntities(
        "r",
        lambda _name, raw_id: raw_id == "duplicate",
        allow_scoped_tool=lambda _name, _raw_id, owner: owner in allowed_parents,
    )
    record = {
        "type": "ai",
        "id": "issuer",
        "tool_calls": [{"id": "duplicate", "name": "task", "args": {"description": "task"}}],
    }
    assert message(entities, record)
    assert not message(entities, record, owner="sibling")
    assert entities.public_delegation_tool_id(None, "duplicate") == "duplicate"
    assert entities.public_delegation_tool_id("sibling", "duplicate") is None
    allowed_parents.clear()
    assert entities.public_tool_id(None, "duplicate") is None
    assert entities.tool_parent_message_id(None, "duplicate") is None
