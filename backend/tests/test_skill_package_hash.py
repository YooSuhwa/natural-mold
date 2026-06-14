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
