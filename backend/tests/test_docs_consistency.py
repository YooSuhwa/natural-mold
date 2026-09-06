"""Contract tests for the tracked documentation inventory checker."""
# allow: SIZE_OK — fixture-driven inventory, link, index, filesystem, and CLI matrix.

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_docs_consistency.py"
ADR_PATH = re.compile(r"docs/design-docs/(?:ADR|adr)-\d{3}-[^/]+\.md$")


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("docs_consistency_checker", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _inventory(entries: list[tuple[str, str, str, str | None]]) -> str:
    lines = ["schema_version: 1", "documents:"]
    for path, lifecycle, status, index in entries:
        lines.extend((f"  - path: {path}", f"    lifecycle: {lifecycle}", f"    status: {status}"))
        if index is not None:
            lines.append(f"    index: {index}")
    return "\n".join(lines) + "\n"


def _base_files() -> dict[str, str]:
    return {
        "README.md": "# Home\n\n[Guide](docs/guide.md#same-heading-1)\n",
        "docs/design-docs/adr-002-second.md": "# Second decision\n",
        "docs/design-docs/adr-010-tenth.md": "# Tenth decision\n",
        "docs/design-docs/index.md": (
            "# ADR Index\n\n"
            "| ADR | Title |\n| --- | --- |\n"
            "| ADR-002 | [Second](adr-002-second.md) |\n"
            "| ADR-010 | [Tenth](adr-010-tenth.md) |\n"
        ),
        "docs/exec-plans/active/work.md": "# Work\n",
        "docs/exec-plans/completed/done.md": "# Done\n",
        "docs/exec-plans/index.md": (
            "# Plans\n\n## Active\n\n- [Work](active/work.md)\n\n"
            "## Completed\n\n- [Done](completed/done.md)\n"
        ),
        "docs/guide.md": "# Same heading\n\n# Same heading\n",
        "docs/PRD.md": "# Product\n",
        "docs/PRD-screens.md": "# Screens\n",
        "docs/references/index.md": (
            "# References\n\n"
            "| Document |\n| --- |\n"
            "| [PRD](../PRD.md) |\n"
            "| [Screens](../PRD-screens.md) |\n"
            "| [Tool guide](../tool-setup-guide.md) |\n"
        ),
        "docs/tool-setup-guide.md": "# Tool setup\n",
    }


def _base_entries() -> list[tuple[str, str, str, str | None]]:
    return [
        ("README.md", "canonical-current", "current", None),
        ("docs/PRD-screens.md", "reference/spec", "reference", "references"),
        ("docs/PRD.md", "reference/spec", "reference", "references"),
        ("docs/design-docs/adr-002-second.md", "decision-record", "accepted", "adr"),
        ("docs/design-docs/adr-010-tenth.md", "decision-record", "accepted", "adr"),
        ("docs/design-docs/index.md", "canonical-current", "current", None),
        ("docs/exec-plans/active/work.md", "active-plan", "active", "active-plan"),
        (
            "docs/exec-plans/completed/done.md",
            "completed-plan",
            "completed",
            "completed-plan",
        ),
        ("docs/exec-plans/index.md", "canonical-current", "current", None),
        ("docs/guide.md", "reference/spec", "reference", None),
        ("docs/references/index.md", "canonical-current", "current", None),
        ("docs/tool-setup-guide.md", "reference/spec", "reference", "references"),
    ]


def _write_repository(repository: Path) -> None:
    for relative_path, content in _base_files().items():
        target = repository / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    inventory = repository / "docs/document-inventory.yaml"
    inventory.write_text(_inventory(_base_entries()), encoding="utf-8")
    subprocess.run(["/usr/bin/git", "init", "--quiet"], cwd=repository, check=True)
    subprocess.run(["/usr/bin/git", "add", "."], cwd=repository, check=True)


@pytest.fixture
def fixture_repository(tmp_path: Path) -> Path:
    # Given a complete tracked documentation repository.
    _write_repository(tmp_path)
    return tmp_path


def test_actual_repository_is_consistent() -> None:
    # Given the actual repository documentation and inventory.
    checker = _load_checker()

    # When the repository is inspected.
    summary = checker.inspect_documentation(SCRIPT_PATH.parents[1])

    # Then every tracked document is classified.
    tracked = (
        subprocess.run(
            ["/usr/bin/git", "ls-files", "-z", "--", "*.md", "*.mdx"],
            cwd=SCRIPT_PATH.parents[1],
            check=True,
            capture_output=True,
        )
        .stdout.decode("utf-8")
        .split("\0")
    )
    tracked_paths = tuple(path for path in tracked if path)
    assert summary.document_count == len(tracked_paths)
    assert summary.adr_count == sum(bool(ADR_PATH.fullmatch(path)) for path in tracked_paths)


def test_complete_fixture_accepts_external_mailto_and_code_links(
    fixture_repository: Path,
) -> None:
    # Given external links and broken-looking Markdown inside code spans and fences.
    checker = _load_checker()
    readme = fixture_repository / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8")
        + "[Same document](#home) [Encoded](docs/guide.md#same%2Dheading)\n"
        + "[Product route](/api/conversations/123)\n"
        + "[Web](https://example.com/a) [Mail](mailto:test@example.com)\n"
        + "linked_message_ids[0](not-a-markdown-link.md)\n"
        + "`[ignored](missing.md)`\n```md\n[ignored](also-missing.md)\n```\n",
        encoding="utf-8",
    )

    # When the repository is inspected.
    summary = checker.inspect_documentation(fixture_repository)

    # Then only tracked documentation participates in the summary.
    assert summary.document_count == 12


def test_four_character_fence_does_not_close_on_inner_three_character_fence(
    fixture_repository: Path,
) -> None:
    # Given a four-backtick fence containing a literal triple-backtick example and link.
    checker = _load_checker()
    readme = fixture_repository / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8") + "````md\n```md\n[ignored](missing.md)\n```\n````\n",
        encoding="utf-8",
    )

    # When the repository is inspected.
    summary = checker.inspect_documentation(fixture_repository)

    # Then the nested example remains code and its link is ignored.
    assert summary.document_count == 12


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        ("missing", "inventory is missing tracked document"),
        ("extra", "inventory contains untracked document"),
        ("duplicate", "duplicate inventory path"),
        ("unsorted", "inventory documents must be sorted"),
        ("non_markdown", "inventory path must end in .md or .mdx"),
        ("unknown_key", "unknown inventory key"),
        ("bad_schema", "schema_version must be 1"),
        ("bad_status", "lifecycle/status mismatch"),
        ("bad_index", "invalid index value"),
        ("absolute", "safe repository-relative POSIX path"),
        ("parent_escape", "safe repository-relative POSIX path"),
        ("backslash", "safe repository-relative POSIX path"),
        ("nul", "safe repository-relative POSIX path"),
        ("wrong_marker_lifecycle", "index markers must match document lifecycle"),
    ],
)
def test_inventory_boundary_rejects_invalid_entries(
    fixture_repository: Path, mutate: str, expected: str
) -> None:
    # Given one malformed or incomplete inventory boundary case.
    checker = _load_checker()
    inventory = fixture_repository / "docs/document-inventory.yaml"
    entries = _base_entries()
    if mutate == "missing":
        entries = entries[1:]
    elif mutate == "extra":
        entries.append(("docs/untracked.md", "reference/spec", "reference", None))
    elif mutate == "duplicate":
        entries.insert(1, entries[0])
    elif mutate == "unsorted":
        entries[0], entries[1] = entries[1], entries[0]
    elif mutate == "non_markdown":
        entries[0] = ("README.txt", "canonical-current", "current", None)
    elif mutate == "unknown_key":
        inventory.write_text(
            _inventory(entries).replace(
                "    status: current\n", "    status: current\n    owner: docs\n", 1
            ),
            encoding="utf-8",
        )
    elif mutate == "bad_schema":
        inventory.write_text(
            _inventory(entries).replace("schema_version: 1", "schema_version: 2"), encoding="utf-8"
        )
    elif mutate == "bad_status":
        entries[0] = ("README.md", "canonical-current", "archived", None)
    elif mutate == "bad_index":
        entries[0] = ("README.md", "canonical-current", "current", "other")
    elif mutate == "absolute":
        entries[0] = ("/README.md", "canonical-current", "current", None)
    elif mutate == "parent_escape":
        entries[0] = ("../README.md", "canonical-current", "current", None)
    elif mutate == "backslash":
        entries[0] = ("docs\\README.md", "canonical-current", "current", None)
    elif mutate == "nul":
        entries[0] = ("README\0.md", "canonical-current", "current", None)
    elif mutate == "wrong_marker_lifecycle":
        entries[6] = (
            "docs/exec-plans/active/work.md",
            "reference/spec",
            "reference",
            "active-plan",
        )
    if mutate not in {"unknown_key", "bad_schema"}:
        inventory.write_text(_inventory(entries), encoding="utf-8")

    # When the checker crosses that boundary, it rejects the invalid state.
    with pytest.raises(checker.DocsConsistencyError, match=expected):
        checker.inspect_documentation(fixture_repository)


@pytest.mark.parametrize(
    ("path", "replacement", "expected"),
    [
        ("README.md", "[Missing](docs/missing.md)\n", "link target does not exist"),
        ("README.md", "[Escape](../outside.md)\n", "link escapes repository"),
        ("README.md", "[Fragment](docs/guide.md#missing)\n", "fragment does not exist"),
        ("README.md", "[Encoded absolute](%2Fprivate/secret.md)\n", "unsafe link destination"),
        ("README.md", "[Encoded escape](%2e%2e/outside.md)\n", "link escapes repository"),
        ("README.md", "[Encoded backslash](docs%5Cguide.md)\n", "unsafe link destination"),
        ("README.md", "[Encoded NUL](docs/guide.md%00)\n", "unsafe link destination"),
        ("README.md", "[Local file](file:///etc/passwd)\n", "unsupported link scheme"),
        ("README.md", "[Script](javascript:alert(1))\n", "unsupported link scheme"),
        (
            "docs/design-docs/index.md",
            "# ADR Index\n\n| ADR | Title |\n| --- | --- |\n"
            "| ADR-002 | [Second](adr-002-second.md) |\n",
            "ADR index must exactly match",
        ),
        (
            "docs/design-docs/index.md",
            "# ADR Index\n\n| ADR | Title |\n| --- | --- |\n"
            "| ADR-002 | [Second](adr-002-second.md) |\n"
            "| ADR-010 | [Tenth](adr-010-tenth.md) |\n"
            "| ADR-010 | [Again](adr-010-tenth.md) |\n",
            "ADR index must exactly match",
        ),
        (
            "docs/design-docs/index.md",
            "# ADR Index\n\n| ADR | Title |\n| --- | --- |\n"
            "| ADR-002 | [Second](adr-002-second.md) |\n"
            "| ADR-010 | [Tenth](adr-010-tenth.md) |\n"
            "| ADR-011 | [Not an ADR](../guide.md) |\n",
            "ADR index must exactly match",
        ),
        (
            "docs/design-docs/index.md",
            "# ADR Index\n\n| ADR | Title |\n| --- | --- |\n"
            "| ADR-010 | [Second](adr-002-second.md) |\n"
            "| ADR-002 | [Tenth](adr-010-tenth.md) |\n",
            "ADR index must exactly match",
        ),
        (
            "docs/exec-plans/index.md",
            "# Plans\n\n## Active\n\n## Completed\n\n- [Done](completed/done.md)\n",
            "Active plan index must exactly match",
        ),
        (
            "docs/exec-plans/index.md",
            "# Plans\n\n## Active\n\n- [Work](active/work.md)\n"
            "- [Again](active/work.md)\n\n"
            "## Completed\n\n- [Done](completed/done.md)\n",
            "Active plan index must exactly match",
        ),
        (
            "docs/exec-plans/index.md",
            "# Plans\n\n## Active\n\n- [Done](completed/done.md)\n\n"
            "## Completed\n\n- [Work](active/work.md)\n",
            "Active plan index must exactly match",
        ),
        (
            "docs/references/index.md",
            "# References\n\n| [PRD](../PRD.md) |\n",
            "references index must exactly match",
        ),
        (
            "docs/references/index.md",
            "# References\n\n| [PRD](../PRD.md) |\n"
            "| [PRD duplicate](../PRD.md) |\n"
            "| [Screens](../PRD-screens.md) |\n"
            "| [Tool guide](../tool-setup-guide.md) |\n",
            "references index must exactly match",
        ),
    ],
)
def test_content_contract_rejects_broken_links_and_indexes(
    fixture_repository: Path, path: str, replacement: str, expected: str
) -> None:
    # Given a broken internal link, fragment, or navigation index.
    checker = _load_checker()
    (fixture_repository / path).write_text(replacement, encoding="utf-8")

    # When checked, the broken navigation contract is rejected.
    with pytest.raises(checker.DocsConsistencyError, match=expected):
        checker.inspect_documentation(fixture_repository)


@pytest.mark.parametrize("kind", ["directory", "symlink", "invalid_utf8"])
def test_tracked_document_must_be_regular_utf8_file(fixture_repository: Path, kind: str) -> None:
    # Given a tracked document replaced by a directory, symlink, or invalid UTF-8.
    checker = _load_checker()
    target = fixture_repository / "docs/guide.md"
    target.unlink()
    if kind == "directory":
        target.mkdir()
    elif kind == "symlink":
        target.symlink_to(fixture_repository / "README.md")
    else:
        target.write_bytes(b"\xff")

    # When checked, the filesystem trust-boundary violation is rejected.
    with pytest.raises(checker.DocsConsistencyError, match="regular UTF-8 file"):
        checker.inspect_documentation(fixture_repository)


def test_cli_reports_bounded_relative_failure_without_traceback(
    fixture_repository: Path,
) -> None:
    # Given a fixture with a broken relative link.
    (fixture_repository / "README.md").write_text("[Missing](missing.md)\n", encoding="utf-8")

    # When the CLI checks that repository.
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--root", str(fixture_repository)],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then it fails concisely without leaking an absolute path or traceback.
    assert result.returncode == 1
    assert "README.md" in result.stderr
    assert str(fixture_repository) not in result.stderr
    assert "Traceback" not in result.stderr
    assert len(result.stderr.splitlines()) <= 22


def test_cli_reports_concise_success(fixture_repository: Path) -> None:
    # Given a complete fixture repository.

    # When the CLI checks that repository.
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--root", str(fixture_repository)],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then it reports the exact classified document count.
    assert result.returncode == 0
    assert result.stdout == "documentation inventory OK: 12 documents, 2 ADRs\n"
    assert result.stderr == ""
