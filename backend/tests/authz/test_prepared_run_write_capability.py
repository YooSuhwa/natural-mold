"""Exact same-run write capability: foreign IDs and path escapes deny before access."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from app.agent_runtime.prepared_run_persistence import PreparedRunPersistence
from app.agent_runtime.prepared_run_write_capability import (
    PreparedRunWriteCapability,
    RunWriteBinding,
    RunWriteCapabilityDeniedError,
)


def _capability(root: Path) -> PreparedRunWriteCapability:
    return PreparedRunWriteCapability(
        RunWriteBinding(
            run_id=uuid.UUID(int=1),
            conversation_id=uuid.UUID(int=2),
            user_id=uuid.UUID(int=3),
            tenant_id=uuid.UUID(int=4),
            org_id=uuid.UUID(int=5),
            output_root=root,
            input_ids=frozenset({uuid.UUID(int=6)}),
            attachment_ids=frozenset({uuid.UUID(int=7)}),
            api_run_id=uuid.UUID(int=8),
        )
    )


def _persistence(
    capability: PreparedRunWriteCapability, calls: list[Any]
) -> PreparedRunPersistence:
    def factory(conversation_id: uuid.UUID, run_id: str):
        calls.append(("factory", conversation_id, run_id))

        async def callback(events: list[dict[str, Any]]) -> None:
            calls.append(("events", run_id, len(events)))

        return callback

    async def finalize(conversation_id: uuid.UUID, run_id: str, **kwargs: Any) -> None:
        calls.append(("trace", conversation_id, run_id, kwargs))

    return PreparedRunPersistence(capability, factory, finalize)


@pytest.mark.asyncio
async def test_exact_writes_reach_injected_writers(tmp_path: Path) -> None:
    calls: list[Any] = []
    persistence = _persistence(_capability(tmp_path), calls)

    callback = persistence.events_callback(uuid.UUID(int=1))
    await callback([{"id": "e1"}])
    await persistence.finalize_trace(str(uuid.UUID(int=1)), success=True)

    assert calls == [
        ("factory", uuid.UUID(int=2), str(uuid.UUID(int=1))),
        ("events", str(uuid.UUID(int=1)), 1),
        (
            "trace",
            uuid.UUID(int=2),
            str(uuid.UUID(int=1)),
            {"tenant": persistence.capability.binding, "success": True},
        ),
    ]
    assert persistence.attachments([uuid.UUID(int=7)]) == (uuid.UUID(int=7),)
    assert persistence.artifact_path("out/report.md") == (tmp_path / "out/report.md").resolve()


@pytest.mark.asyncio
async def test_denies_foreign_ids_reads_and_path_escape(tmp_path: Path) -> None:
    calls: list[Any] = []
    capability = _capability(tmp_path / "root")
    (tmp_path / "root").mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "root" / "link").symlink_to(tmp_path / "outside")
    persistence = _persistence(capability, calls)
    foreign = uuid.UUID(int=99)

    denied: list[Any] = [
        lambda: persistence.events_callback(foreign),
        lambda: capability.require_input(foreign),
        lambda: capability.require_conversation(foreign),
        lambda: capability.require_envelope("trigger", uuid.UUID(int=8)),
        lambda: capability.require_envelope("api", foreign),
        lambda: persistence.attachments([foreign]),
        lambda: capability.require_run("not-a-uuid"),
        lambda: capability.require_envelope("other", uuid.UUID(int=8)),  # type: ignore[arg-type]
    ]
    for case in denied:
        with pytest.raises(RunWriteCapabilityDeniedError) as error:
            case()
        assert error.value.code == "run_write_capability_denied"
    for path in (
        "../escape",
        "/etc/passwd",
        "a/../../b",
        "link/secret",
        "",
        "nul\x00byte",
        ".",
        "./",
    ):
        with pytest.raises(RunWriteCapabilityDeniedError):
            capability.output_path(path)
    with pytest.raises(RunWriteCapabilityDeniedError):
        await persistence.finalize_trace(foreign, success=True)
    assert calls == []
