"""Adversarial inode-binding tests for final-attempt filesystem writes."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import final_attempt_io as io_module  # noqa: E402
from final_attempt_io import (  # noqa: E402
    LifecycleError,
    bind_final_gate_output,
    write_bound_final_gate_output,
)
from operation_ledger_format import canonical_line  # noqa: E402
from operation_ledger_writer import evidence_writer_lock  # noqa: E402
from plan_history_support import _begin, lifecycle_arguments  # noqa: E402


@pytest.mark.parametrize(
    "boundary",
    ["begin:attempt_directory:directory_fsync", "begin:attempt_directory:parent_fsync"],
)
def test_create_directory_rejects_post_fsync_name_swap_without_deleting_foreign_directory(
    tmp_path: Path, boundary: str
) -> None:
    lifecycle = lifecycle_arguments(tmp_path)
    attempt_root = Path(lifecycle["attempt_root"])
    foreign = tmp_path / "foreign-attempt"
    foreign.mkdir()
    (foreign / "foreign-marker").write_text("foreign\n", encoding="utf-8")
    displaced = tmp_path / "owned-attempt"

    def swap(stage: str) -> None:
        if stage == boundary:
            created = next(attempt_root.iterdir())
            created.rename(displaced)
            foreign.rename(created)

    with pytest.raises(LifecycleError, match="directory identity changed"):
        _begin(lifecycle, hook=swap)

    visible = next(attempt_root.iterdir())
    assert (visible / "foreign-marker").read_text(encoding="utf-8") == "foreign\n"
    assert displaced.is_dir()


@pytest.mark.parametrize("attack", ["before_rename", "after_rename", "after_parent_fsync"])
def test_bound_output_rejects_name_swap_and_preserves_substituted_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str
) -> None:
    lifecycle = lifecycle_arguments(tmp_path)
    pointer = _begin(lifecycle)
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    output = attempt_dir / "f1-history.json"
    foreign = attempt_dir / "foreign.json"
    foreign_payload = canonical_line({"foreign": True})
    foreign.write_bytes(foreign_payload)
    foreign.chmod(0o600)
    saved = attempt_dir / "owned-output"
    original_fsync = io_module.os.fsync
    original_replace = io_module.os.replace
    swapped = False

    with (
        evidence_writer_lock(Path(lifecycle["operations"])) as evidence_lock,
        bind_final_gate_output(evidence_lock, attempt_dir) as binding,
    ):

        def swap_on_fsync(descriptor: int) -> None:
            nonlocal swapped
            original_fsync(descriptor)
            if swapped:
                return
            if attack == "before_rename" and stat.S_ISREG(os.fstat(descriptor).st_mode):
                temporary = next(attempt_dir.glob(".f1-history.json.tmp-*"))
                original_replace(
                    temporary.name,
                    saved.name,
                    src_dir_fd=binding.descriptor,
                    dst_dir_fd=binding.descriptor,
                )
                original_replace(
                    foreign.name,
                    temporary.name,
                    src_dir_fd=binding.descriptor,
                    dst_dir_fd=binding.descriptor,
                )
                swapped = True
            elif attack == "after_parent_fsync" and descriptor == binding.descriptor:
                original_replace(
                    output.name,
                    saved.name,
                    src_dir_fd=binding.descriptor,
                    dst_dir_fd=binding.descriptor,
                )
                original_replace(
                    foreign.name,
                    output.name,
                    src_dir_fd=binding.descriptor,
                    dst_dir_fd=binding.descriptor,
                )
                swapped = True

        def swap_after_replace(
            source: str,
            destination: str,
            *,
            src_dir_fd: int | None = None,
            dst_dir_fd: int | None = None,
        ) -> None:
            nonlocal swapped
            original_replace(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )
            if attack == "after_rename" and destination == output.name:
                original_replace(
                    output.name,
                    saved.name,
                    src_dir_fd=binding.descriptor,
                    dst_dir_fd=binding.descriptor,
                )
                original_replace(
                    foreign.name,
                    output.name,
                    src_dir_fd=binding.descriptor,
                    dst_dir_fd=binding.descriptor,
                )
                swapped = True

        monkeypatch.setattr(io_module.os, "fsync", swap_on_fsync)
        monkeypatch.setattr(io_module.os, "replace", swap_after_replace)

        with pytest.raises(LifecycleError, match="output write failed closed|identity"):
            write_bound_final_gate_output(binding, output, {"schema_version": 1})

    assert swapped
    assert saved.is_file()
    if attack == "before_rename":
        substituted = next(attempt_dir.glob(".f1-history.json.tmp-*"))
        assert substituted.read_bytes() == foreign_payload
        assert not output.exists()
    else:
        assert output.read_bytes() == foreign_payload
