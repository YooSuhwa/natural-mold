"""Unit tests for ``app.storage.paths`` (ADR-018).

These verify the helper alone, decoupled from skill or marketplace
service code. A separate regression test enforces that production
write sites never persist absolute paths — see
``tests/test_skill_storage_relative.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.storage.paths import ensure_relative, resolve_data_path


def test_resolve_relative_against_data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    got = resolve_data_path("skills/abc/SKILL.md")
    assert got == (tmp_path / "skills" / "abc" / "SKILL.md").resolve()


def test_resolve_absolute_passes_through(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    abs_input = "/var/legacy/skills/x"
    assert resolve_data_path(abs_input) == Path(abs_input)


def test_resolve_empty_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    with pytest.raises(ValueError):
        resolve_data_path("")


def test_ensure_relative_accepts_relative():
    assert ensure_relative("skills/abc") == "skills/abc"


def test_ensure_relative_rejects_absolute():
    with pytest.raises(ValueError, match="must be relative"):
        ensure_relative("/abs/path")


def test_ensure_relative_rejects_empty():
    with pytest.raises(ValueError):
        ensure_relative("")
