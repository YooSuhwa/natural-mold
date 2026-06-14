from __future__ import annotations

from pathlib import Path

import pytest

from app.skills.package_hash import PackageHashError, compute_package_tree_hash


def _write_package(root: Path, files: dict[str, str]) -> None:
    for rel_path, content in files.items():
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def test_package_tree_hash_is_deterministic_for_same_tree(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_package(
        first,
        {
            "SKILL.md": "name: demo\n",
            "scripts/run.py": "print('hello')\n",
        },
    )
    _write_package(
        second,
        {
            "scripts/run.py": "print('hello')\n",
            "SKILL.md": "name: demo\n",
        },
    )

    digest = compute_package_tree_hash(first)

    assert len(digest) == 64
    assert digest == compute_package_tree_hash(second)


def test_package_tree_hash_changes_when_path_or_bytes_change(tmp_path: Path) -> None:
    base = tmp_path / "base"
    changed_bytes = tmp_path / "changed-bytes"
    changed_path = tmp_path / "changed-path"
    _write_package(base, {"SKILL.md": "name: demo\n", "scripts/run.py": "print(1)\n"})
    _write_package(
        changed_bytes,
        {"SKILL.md": "name: demo\n", "scripts/run.py": "print(2)\n"},
    )
    _write_package(changed_path, {"SKILL.md": "name: demo\n", "scripts/main.py": "print(1)\n"})

    digest = compute_package_tree_hash(base)

    assert digest != compute_package_tree_hash(changed_bytes)
    assert digest != compute_package_tree_hash(changed_path)


def test_package_tree_hash_tracks_portable_skill_surfaces(tmp_path: Path) -> None:
    base_files = {
        "SKILL.md": "name: demo\n",
        "scripts/run.py": "print(1)\n",
        "references/guide.md": "follow this\n",
        "agents/openai.yaml": "version: 1\n",
    }
    base = tmp_path / "base"
    changed_skill = tmp_path / "changed-skill"
    changed_reference = tmp_path / "changed-reference"
    changed_agent_metadata = tmp_path / "changed-agent-metadata"
    _write_package(base, base_files)
    _write_package(changed_skill, {**base_files, "SKILL.md": "name: better\n"})
    _write_package(
        changed_reference,
        {**base_files, "references/guide.md": "follow this carefully\n"},
    )
    _write_package(
        changed_agent_metadata,
        {**base_files, "agents/openai.yaml": "version: 2\n"},
    )

    digest = compute_package_tree_hash(base)

    assert digest != compute_package_tree_hash(changed_skill)
    assert digest != compute_package_tree_hash(changed_reference)
    assert digest != compute_package_tree_hash(changed_agent_metadata)


def test_package_tree_hash_ignores_transient_files(tmp_path: Path) -> None:
    clean = tmp_path / "clean"
    noisy = tmp_path / "noisy"
    files = {"SKILL.md": "name: demo\n", "scripts/run.py": "print(1)\n"}
    _write_package(clean, files)
    _write_package(
        noisy,
        {
            **files,
            ".DS_Store": "finder",
            "notes.txt~": "swap",
            ".moldy-runtime/result.json": "runtime output",
        },
    )

    assert compute_package_tree_hash(clean) == compute_package_tree_hash(noisy)


def test_package_tree_hash_rejects_symlink(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    _write_package(root, {"SKILL.md": "name: demo\n"})
    (root / "linked.md").symlink_to(root / "SKILL.md")

    with pytest.raises(PackageHashError, match="symlink"):
        compute_package_tree_hash(root)
