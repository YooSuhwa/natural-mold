"""Input-validation tests for the openwiki ``sync_repo.py`` skill script.

The agent (LLM) supplies ``--repo-url`` / ``--ref``, so the script must
reject git option injection (leading ``-``), non-http(s) transports
(``ext::`` command execution, ``file://`` local reads) and refs that
could smuggle flags. Added while enabling the ruff ``S`` rules (S603).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "app/seed/system_skill_packages/openwiki/scripts/sync_repo.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("openwiki_sync_repo", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sync_repo() -> ModuleType:
    return _load_module()


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/langchain-ai/openwiki.git",
        "http://internal.example/repo.git",
        "HTTPS://Github.com/Org/Repo.git",  # scheme is case-insensitive (RFC 3986)
    ],
)
def test_valid_repo_urls_pass(sync_repo: ModuleType, url: str) -> None:
    sync_repo._validate_repo_url(url)  # does not raise


@pytest.mark.parametrize(
    "url",
    [
        "--upload-pack=touch /tmp/pwned",  # git option injection
        "-o=evil",
        "ext::sh -c id",  # command-executing transport
        "file:///etc/passwd",  # local file read
        "git@github.com:a/b.git",  # ssh — no keys in the skill sandbox
        "https://host/repo with space",
        "https://host/re\x00po",  # embedded NUL — clean-fail instead of subprocess ValueError
        "httpſ://host/repo",  # U+017F case-folds to "s" without re.ASCII
        "",
    ],
)
def test_dangerous_repo_urls_rejected(sync_repo: ModuleType, url: str) -> None:
    with pytest.raises(SystemExit):
        sync_repo._validate_repo_url(url)


@pytest.mark.parametrize("ref", [None, "main", "feature/x", "v1.2.3", "release_2026.07"])
def test_valid_refs_pass(sync_repo: ModuleType, ref: str | None) -> None:
    sync_repo._validate_ref(ref)  # does not raise


@pytest.mark.parametrize(
    "ref",
    [
        "--upload-pack=/tmp/x",  # option injection via ref
        "-b",
        "a b",
        "ref;rm -rf /",
        "",
    ],
)
def test_dangerous_refs_rejected(sync_repo: ModuleType, ref: str) -> None:
    with pytest.raises(SystemExit):
        sync_repo._validate_ref(ref)
