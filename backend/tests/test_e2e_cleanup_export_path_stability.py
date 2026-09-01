"""Regression tests for pinned E2E export trust-root validation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from e2e_cleanup_export_paths import _PinnedDirectory  # noqa: E402
from postgres_cleanup_checker import ManifestValidationError  # noqa: E402


def test_pinned_directory_allows_unrelated_outer_ancestor_churn(tmp_path: Path) -> None:
    # Given a trusted root below an outer directory shared by unrelated work.
    trust_root = tmp_path / "trusted-root"
    trust_root.mkdir()
    unrelated_sibling = tmp_path.parent / f"{tmp_path.name}-unrelated"

    with _PinnedDirectory(trust_root) as pinned:
        # When an unrelated sibling changes only the shared outer ancestor.
        unrelated_sibling.mkdir()
        try:
            # Then the pinned trust root remains valid.
            pinned.validate("export_directory")
        finally:
            unrelated_sibling.rmdir()


def test_pinned_directory_rejects_pinned_root_swap_back(tmp_path: Path) -> None:
    # Given an explicit pinned trust root and a same-parent replacement directory.
    trust_root = tmp_path / "trusted-root"
    replacement = tmp_path / "attacker-root"
    saved = tmp_path / "saved-root"
    trust_root.mkdir()
    replacement.mkdir()

    with _PinnedDirectory(trust_root) as pinned:
        # When the trust root is renamed away, replaced, and restored before validation.
        trust_root.rename(saved)
        replacement.rename(trust_root)
        trust_root.rename(replacement)
        saved.rename(trust_root)

        # Then its change identity still fails closed.
        with pytest.raises(ManifestValidationError, match="export_directory"):
            pinned.validate("export_directory")
