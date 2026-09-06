from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import pytest

from app.agent_runtime import offload_storage_fd
from app.agent_runtime.offload_storage import (
    OffloadIdentity,
    OffloadSecurityError,
    ScopedOffloadStorage,
    delete_conversation_offloads,
)
from app.agent_runtime.offload_storage_lifecycle import gc_spill_run
from app.agent_runtime.offload_storage_types import OffloadGcScope, token


def _construct(data_dir: Path) -> None:
    ScopedOffloadStorage(
        data_dir=data_dir,
        identity=OffloadIdentity("user-a", "conversation-a", "run-a"),
    ).for_actor(UUID("11111111-1111-4111-8111-111111111111"))


def _delete(data_dir: Path) -> None:
    delete_conversation_offloads(
        data_dir,
        owner_id="user-a",
        conversation_id="conversation-a",
    )


def _gc(data_dir: Path) -> None:
    gc_spill_run(
        OffloadGcScope(
            internal_root=data_dir / ".moldy-internal" / "offload",
            owner=token("owner", "user-a"),
            conversation=token("conversation", "conversation-a"),
            run=token("run", "run-a"),
        )
    )


@pytest.mark.parametrize("operation", [_construct, _delete, _gc])
def test_public_root_rejects_constructor_delete_and_gc_without_mutation(
    tmp_path: Path,
    operation: Callable[[Path], None],
) -> None:
    private_root = tmp_path / ".moldy-internal"
    private_root.mkdir(mode=0o700)
    private_root.chmod(0o777)

    with pytest.raises(OffloadSecurityError, match="internal offload root must be private"):
        operation(tmp_path)

    assert not (private_root / "offload").exists()
    assert not (private_root / "gc-lock").exists()


@pytest.mark.parametrize("operation", [_construct, _delete, _gc])
def test_wrong_owner_rejects_constructor_delete_and_gc_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: Callable[[Path], None],
) -> None:
    private_root = tmp_path / ".moldy-internal"
    private_root.mkdir(mode=0o700)
    real_euid = os.geteuid()
    monkeypatch.setattr(offload_storage_fd.os, "geteuid", lambda: real_euid + 1)

    with pytest.raises(OffloadSecurityError, match="internal offload root must be private"):
        operation(tmp_path)

    assert not (private_root / "offload").exists()
    assert not (private_root / "gc-lock").exists()
