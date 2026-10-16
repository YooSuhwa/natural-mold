"""Frozen snapshot reads and exact run-root writes for prepared leases."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import PERMISSION_DENIED

from app.agent_runtime import runtime_snapshot_catalog
from app.agent_runtime.lease_snapshot_backend import (
    READ_ONLY_ERROR,
    LeaseSnapshotBackend,
    LeaseSnapshotBackendError,
)
from app.agent_runtime.runtime_snapshot_catalog import (
    RuntimeSnapshotCatalog,
    SnapshotCatalogBuilder,
    SnapshotCatalogError,
)

SKILLS = "/runtime/thread-1/skills"
MEMORY = "/agents/agent-1/AGENTS.md"


def _sources(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    skill = data / "skills" / "writer"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: writer\n---\noriginal skill\n")
    (skill / "scripts" / "run.py").write_text("print('original')\n")
    (data / "agents" / "agent-1").mkdir(parents=True)
    (data / "agents" / "agent-1" / "AGENTS.md").write_text("original memory\n")
    return data


def _catalog(data: Path, resource: bytearray) -> RuntimeSnapshotCatalog:
    builder = SnapshotCatalogBuilder()
    assert builder.add_tree(f"{SKILLS}/writer", root=data / "skills" / "writer") == 2
    builder.add_file(
        MEMORY, root=data, source=data / "agents" / "agent-1" / "AGENTS.md", kind="memory"
    )
    builder.add_resource("doc-1", resource)
    builder.add_bytes("/history/allowed.json", b'["hello"]', kind="history")
    return builder.build()


def _backend(tmp_path: Path, catalog: RuntimeSnapshotCatalog) -> tuple[LeaseSnapshotBackend, Path]:
    run_root = tmp_path / "runs" / "run-1"
    roots = {name: run_root / name for name in ("scratch", "offload", "output")}
    for path in roots.values():
        path.mkdir(parents=True)
    backend = LeaseSnapshotBackend(
        catalog,
        run_root=run_root,
        writable_roots={f"/run/{name}": path for name, path in roots.items()},
        forbidden_root=tmp_path / "data",
        artifacts_root="/run/offload",
    )
    return backend, run_root


def _read(backend: LeaseSnapshotBackend, path: str) -> str | None:
    result = backend.read(path)
    return None if result.file_data is None else result.file_data["content"]


def test_mutating_sources_after_preparation_does_not_change_frozen_reads(
    tmp_path: Path,
) -> None:
    data = _sources(tmp_path)
    resource = bytearray(b"resource v1")
    catalog = _catalog(data, resource)
    backend, _run_root = _backend(tmp_path, catalog)

    (data / "skills" / "writer" / "SKILL.md").write_text("tampered skill\n")
    (data / "skills" / "writer" / "scripts" / "run.py").unlink()
    (data / "skills" / "writer" / "new.md").write_text("injected\n")
    (data / "agents" / "agent-1" / "AGENTS.md").write_text("tampered memory\n")
    resource[:] = b"resource v2 (row updated)"

    assert "original skill" in (_read(backend, f"{SKILLS}/writer/SKILL.md") or "")
    assert _read(backend, f"{SKILLS}/writer/scripts/run.py") == "print('original')\n"
    assert _read(backend, f"{SKILLS}/writer/new.md") is None
    assert _read(backend, MEMORY) == "original memory\n"
    assert _read(backend, "/resources/doc-1") == "resource v1"
    assert _read(backend, "/history/allowed.json") == '["hello"]'
    downloaded = backend.download_files(["/resources/doc-1"])[0]
    assert downloaded.content == b"resource v1"
    assert catalog.resource_ids == frozenset({"doc-1"})
    rebuilt = _catalog(data, resource)
    assert rebuilt.digest != catalog.digest
    assert catalog.digests()[MEMORY] != rebuilt.digests()[MEMORY]


def test_snapshot_paths_are_read_only_and_writes_land_only_in_run_roots(
    tmp_path: Path,
) -> None:
    data = _sources(tmp_path)
    backend, run_root = _backend(tmp_path, _catalog(data, bytearray(b"r")))

    assert backend.write(MEMORY, "overwrite").error == READ_ONLY_ERROR
    assert backend.edit(MEMORY, "original", "evil").error == READ_ONLY_ERROR
    assert backend.delete(f"{SKILLS}/writer/SKILL.md").error == READ_ONLY_ERROR
    assert backend.write("/new-root-file.txt", "x").error == READ_ONLY_ERROR
    assert backend.upload_files([("/resources/doc-2", b"x")])[0].error is not None
    assert _read(backend, MEMORY) == "original memory\n"

    assert backend.write("/run/output/report.md", "result").error is None
    assert (run_root / "output" / "report.md").read_text() == "result"
    assert _read(backend, "/run/output/report.md") == "result"
    assert asyncio.run(backend.awrite("/run/scratch/tmp.txt", "s")).error is None
    assert (run_root / "scratch" / "tmp.txt").read_text() == "s"
    assert backend.artifacts_root == "/run/offload"
    assert (data / "agents" / "agent-1" / "AGENTS.md").read_text() == "original memory\n"


@pytest.mark.parametrize(
    "path",
    [
        "/run/output/../../../agents/agent-1/AGENTS.md",
        "/run/scratch/../offload/../../../skills/writer/SKILL.md",
        "~/agents/agent-1/AGENTS.md",
        "/../data/agents/agent-1/AGENTS.md",
        "/skills/writer/SKILL.md",
        "/agents/agent-1/../agent-1/AGENTS.md",
    ],
)
def test_shared_root_and_path_escapes_are_unreachable(tmp_path: Path, path: str) -> None:
    data = _sources(tmp_path)
    backend, _run_root = _backend(tmp_path, _catalog(data, bytearray(b"r")))
    before = sorted(p.relative_to(data).as_posix() for p in data.rglob("*"))

    assert _read(backend, path) is None
    write = backend.write(path, "escaped")
    assert write.error is not None
    after = sorted(p.relative_to(data).as_posix() for p in data.rglob("*"))
    assert before == after


def test_symlink_inside_run_root_cannot_escape(tmp_path: Path) -> None:
    data = _sources(tmp_path)
    backend, run_root = _backend(tmp_path, _catalog(data, bytearray(b"r")))
    (run_root / "output" / "link").symlink_to(data / "agents")

    assert _read(backend, "/run/output/link/agent-1/AGENTS.md") is None
    assert backend.write("/run/output/link/agent-1/AGENTS.md", "x").error is not None
    assert (data / "agents" / "agent-1" / "AGENTS.md").read_text() == "original memory\n"


def _build(catalog, run_root: Path, roots: dict[str, Path], data: Path) -> None:
    LeaseSnapshotBackend(catalog, run_root=run_root, writable_roots=roots, forbidden_root=data)


def test_run_root_cannot_be_inside_equal_or_contain_the_shared_root(tmp_path: Path) -> None:
    data = _sources(tmp_path)
    catalog = _catalog(data, bytearray(b"r"))
    for run_root in (data, tmp_path, data / "runs" / "run-1"):
        (run_root / "out").mkdir(parents=True, exist_ok=True)
        with pytest.raises(LeaseSnapshotBackendError) as denied:
            _build(catalog, run_root, {"/run/output": run_root / "out"}, data)
        assert denied.value.code == "lease_backend_root_shared"


def test_writable_roots_are_strictly_under_run_root_and_disjoint(tmp_path: Path) -> None:
    data = _sources(tmp_path)
    catalog = _catalog(data, bytearray(b"r"))
    run_root = tmp_path / "runs" / "run-1"
    (run_root / "a" / "b").mkdir(parents=True)
    (tmp_path / "elsewhere" / "x").mkdir(parents=True)
    (run_root / "link").symlink_to(tmp_path / "elsewhere")
    cases = [
        ({"/run/output": tmp_path / "elsewhere"}, "lease_backend_root_outside_run"),
        ({"/run/output": run_root}, "lease_backend_root_outside_run"),
        ({"/run/output": run_root / "link" / "x"}, "lease_backend_root_outside_run"),
        ({"/run/a": run_root / "a", "/run/b": run_root / "a" / "b"}, "lease_backend_root_nested"),
        ({"/run/b": run_root / "a" / "b", "/run/a": run_root / "a"}, "lease_backend_root_nested"),
        ({"/resources": run_root / "a"}, "lease_backend_root_overlaps_snapshot"),
    ]
    for roots, code in cases:
        with pytest.raises(LeaseSnapshotBackendError) as denied:
            _build(catalog, run_root, roots, data)
        assert denied.value.code == code, roots
    with pytest.raises(LeaseSnapshotBackendError) as missing:
        _build(catalog, tmp_path / "missing", {}, data)
    assert missing.value.code == "lease_backend_root_invalid"


def test_run_root_os_errors_become_error_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, _run_root = _backend(tmp_path, _catalog(_sources(tmp_path), bytearray(b"r")))

    def broken(*args: object, **kwargs: object) -> object:
        raise OSError(36, "File name too long")

    for method in ("read", "write", "ls", "glob"):
        monkeypatch.setattr(FilesystemBackend, method, broken)
    assert backend.read("/run/output/x").error == PERMISSION_DENIED
    assert backend.write("/run/output/x", "y").error == PERMISSION_DENIED
    assert backend.ls("/run/output").error == PERMISSION_DENIED
    assert backend.glob("*", "/run/output").error == PERMISSION_DENIED


def test_catalog_rejects_hardlinks_and_files_swapped_after_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = _sources(tmp_path)
    skill = data / "skills" / "writer"
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    os.link(secret, skill / "hardlink.md")
    with pytest.raises(SnapshotCatalogError) as linked:
        SnapshotCatalogBuilder().add_file(
            "/x", root=data, source=skill / "hardlink.md", kind="skill"
        )
    assert linked.value.code == "snapshot_source_hardlinked"
    (skill / "hardlink.md").unlink()

    real_open = os.open
    replacement = tmp_path / "replacement.md"

    def swapping_open(path: object, flags: int, *args: object) -> int:
        replacement.write_text("swapped in after the checks")
        replacement.replace(skill / "SKILL.md")
        return real_open(path, flags, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(runtime_snapshot_catalog.os, "open", swapping_open)
    with pytest.raises(SnapshotCatalogError) as swapped:
        SnapshotCatalogBuilder().add_file("/x", root=data, source=skill / "SKILL.md", kind="skill")
    assert swapped.value.code == "snapshot_source_changed"


def test_catalog_rejects_symlinks_escapes_and_bad_virtual_paths(tmp_path: Path) -> None:
    data = _sources(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    (data / "skills" / "writer" / "leak.md").symlink_to(secret)
    builder = SnapshotCatalogBuilder()

    with pytest.raises(SnapshotCatalogError) as tree:
        builder.add_tree("/skills/writer", root=data / "skills" / "writer")
    assert tree.value.code == "snapshot_source_symlink"
    with pytest.raises(SnapshotCatalogError) as outside:
        builder.add_file("/x", root=data, source=secret, kind="memory")
    assert outside.value.code == "snapshot_source_outside_root"
    for bad in ("relative", "/a/../b", "/a/./b", "/a//b", "/a\\b", "/"):
        with pytest.raises(SnapshotCatalogError):
            builder.add_bytes(bad, b"x", kind="history")
    with pytest.raises(SnapshotCatalogError):
        builder.add_resource("../doc", b"x")
    builder.build()
    with pytest.raises(SnapshotCatalogError) as sealed:
        builder.add_bytes("/late", b"x", kind="history")
    assert sealed.value.code == "snapshot_sealed"
