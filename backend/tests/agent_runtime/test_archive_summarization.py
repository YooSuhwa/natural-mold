from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.agent_runtime import history_archive, runtime_component_builder
from app.agent_runtime.archive_summarization import ArchiveSummarizationMiddleware
from app.agent_runtime.offload_storage import OffloadIdentity, ScopedOffloadBackend
from app.agent_runtime.runtime_agent_builder import build_agent
from app.agent_runtime.runtime_policy import resolve_runtime_policy


class _Model(GenericFakeChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> _Model:
        return self


def _agent(
    backend: ScopedOffloadBackend,
    saver: InMemorySaver,
    *,
    stored: bool,
    archive: bool | None,
) -> Any:
    model = _Model(messages=itertools.cycle(["비교한 결과"]))
    model.profile = {"max_input_tokens": 12_000}
    policy = (
        resolve_runtime_policy(
            {
                "version": 1,
                "filesystem": {"mode": "artifact_write"},
                "todo": {"enabled": True},
                "summarization": {"mode": "auto"},
            }
        )
        if stored
        else None
    )
    return build_agent(
        model,
        [],
        "정확하게 답하세요.",
        backend=backend,
        checkpointer=saver,
        runtime_policy=policy,
        archive_chat_history=archive,
    )


def _input(turn: int) -> dict[str, list[HumanMessage]]:
    return {
        "messages": [
            HumanMessage(
                id=f"turn-{turn}",
                content=f"원문-{turn}\n" + "word " * 4400,
                additional_kwargs={"original": f"조건 {turn}번 🌱"},
            )
        ]
    }


@pytest.mark.parametrize("stored", [False, True])
async def test_real_root_checkpoints_restore_after_save_failure_on_both_policies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stored: bool
) -> None:
    backend = ScopedOffloadBackend(tmp_path, OffloadIdentity("owner", "thread", "run"), uuid4())
    agent = _agent(backend, InMemorySaver(), stored=stored, archive=True)
    config: Any = {"configurable": {"thread_id": "thread"}}
    await agent.ainvoke(_input(1), config)
    original_write = history_archive.atomic_write_new_or_equal
    chunk_writes: list[Path] = []
    monkeypatch.setattr(history_archive, "CHUNK_BYTES", 4096)

    def fail_third(path: Path, content: bytes) -> None:
        chunk_writes.append(path)
        if len(chunk_writes) == 3:
            raise OSError("archive storage offline")
        original_write(path, content)

    with monkeypatch.context() as fault:
        fault.setattr(history_archive, "atomic_write_new_or_equal", fail_third)
        with pytest.raises(OSError, match="archive storage offline"):
            await agent.ainvoke(_input(2), config)
    assert len(chunk_writes) == 3
    state = await agent.aget_state(config)
    assert not state.values.get("_summarization_event")
    assert sum(isinstance(m, AIMessage) for m in state.values["messages"]) == 1

    # Retry the failed checkpoint unchanged, then continue the same chat. The archive
    # restores exactly the state messages below its latest cutoff, including metadata.
    await agent.ainvoke(None, config)
    await agent.ainvoke(_input(3), config)
    state = await agent.aget_state(config)
    event = state.values["_summarization_event"]
    originals = state.values["messages"][: event["cutoff_index"]]
    expected = b"".join(m.model_dump_json().encode() + b"\n" for m in originals)
    assert event["file_path"].startswith(f"{backend.artifacts_root}/conversation_history/archive_")
    assert b"".join(backend.iter_archive_bytes(event["file_path"])) == expected


async def test_disabled_writes_then_reenabled_preserve_exact_checkpoint_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = ScopedOffloadBackend(tmp_path, OffloadIdentity("owner", "thread", "run"), uuid4())
    saver = InMemorySaver()
    config: Any = {"configurable": {"thread_id": "thread"}}
    enabled = _agent(backend, saver, stored=True, archive=True)
    await enabled.ainvoke(_input(1), config)
    await enabled.ainvoke(_input(2), config)
    first = (await enabled.aget_state(config)).values["_summarization_event"]["file_path"]
    first_data = b"".join(backend.iter_archive_bytes(first))

    disabled = _agent(backend, saver, stored=True, archive=False)
    with monkeypatch.context() as no_writes:
        no_writes.setattr(
            backend, "archive_compaction", lambda *a, **k: pytest.fail("archive write")
        )
        await disabled.ainvoke(_input(3), config)
        await disabled.ainvoke(_input(4), config)
        state = (await disabled.aget_state(config)).values
        legacy = state["_summarization_event"]["file_path"]
        assert legacy.rsplit("/", 1)[-1].startswith("session_")
        rendered = backend.read(legacy)
        assert rendered.file_data and rendered.error is None
        text = rendered.file_data["content"]
        assert all(f"원문-{turn}" in text for turn in (1, 2, 3))
        assert b"".join(backend.iter_archive_bytes(first)) == first_data

    await enabled.ainvoke(_input(5), config)
    state = (await enabled.aget_state(config)).values
    latest = state["_summarization_event"]
    expected = b"".join(
        m.model_dump_json().encode() + b"\n" for m in state["messages"][: latest["cutoff_index"]]
    )
    assert b"".join(backend.iter_archive_bytes(latest["file_path"])) == expected


@pytest.mark.parametrize("stored", [False, True])
def test_archiving_only_replaces_trusted_root_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stored: bool
) -> None:
    backend = ScopedOffloadBackend(tmp_path, OffloadIdentity("owner", "thread", "run"), uuid4())
    captured = MagicMock()
    monkeypatch.setattr(runtime_component_builder, "create_deep_agent", captured)
    for mode in (True, False, None):
        _agent(backend, InMemorySaver(), stored=stored, archive=mode)
        args = captured.call_args.kwargs
        root = [m for m in args["middleware"] if isinstance(m, ArchiveSummarizationMiddleware)]
        assert [m.writes_archives for m in root] == ([] if mode is None else [mode])
        assert args["subagents"]
        for child in args["subagents"]:
            assert all(
                not isinstance(m, ArchiveSummarizationMiddleware) for m in child["middleware"]
            )
