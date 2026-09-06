#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# ///
# ─── How to run ───
# cd backend && uv run python ../scripts/check_docs_consistency.py
# allow: SIZE_OK — the stdlib-only repository gate is intentionally self-contained.
"""Validate the tracked Markdown inventory, navigation indexes, and local links."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final
from urllib.parse import SplitResult, unquote, urlsplit

INVENTORY_PATH: Final = PurePosixPath("docs/document-inventory.yaml")
LIFECYCLE_STATUS: Final = {
    "canonical-current": "current",
    "decision-record": "accepted",
    "active-plan": "active",
    "completed-plan": "completed",
    "reference/spec": "reference",
    "historical": "archived",
    "generated-evidence": "generated",
}
INDEX_VALUES: Final = frozenset({"adr", "active-plan", "completed-plan", "references"})
EXTERNAL_SCHEMES: Final = frozenset({"http", "https", "mailto"})
ENTRY_KEYS: Final = frozenset({"path", "lifecycle", "status", "index"})
LINK: Final = re.compile(r"(?<![\w.])!?\[[^\]]*\]\(([^)]+)\)")
HEADING: Final = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
ADR_PATH: Final = re.compile(r"docs/design-docs/(?:ADR|adr)-(\d{3})-[^/]+\.md$")
ADR_ROW: Final = re.compile(r"^\|\s*ADR-(\d{3})\s*\|.*?\]\(([^)]+)\)", re.MULTILINE)
MAX_ERRORS: Final = 20
GIT: Final = "/usr/bin/git"


@dataclass(frozen=True, slots=True)
class DocumentEntry:
    path: PurePosixPath
    lifecycle: str
    status: str
    index: str | None


@dataclass(frozen=True, slots=True)
class DocumentationSummary:
    document_count: int
    adr_count: int


@dataclass(slots=True)
class DocsConsistencyError(RuntimeError):
    messages: tuple[str, ...]

    def __str__(self) -> str:
        shown = self.messages[:MAX_ERRORS]
        suffix = (
            f"\n... {len(self.messages) - MAX_ERRORS} more error(s)"
            if len(self.messages) > MAX_ERRORS
            else ""
        )
        return "\n".join(shown) + suffix


def _fail(message: str) -> None:
    raise DocsConsistencyError((message,))


def _tracked_markdown(root: Path) -> tuple[PurePosixPath, ...]:
    try:
        result = subprocess.run(  # noqa: S603 -- fixed /usr/bin/git, no user-controlled argv
            [GIT, "ls-files", "-z", "--", "*.md", "*.mdx"],
            cwd=root,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        _fail("unable to enumerate tracked Markdown documents")
    try:
        values = result.stdout.decode("utf-8").split("\0")
    except UnicodeDecodeError:
        _fail("tracked Markdown paths must be UTF-8")
    return tuple(PurePosixPath(value) for value in values if value)


def _parse_inventory(text: str) -> tuple[DocumentEntry, ...]:
    lines = text.splitlines()
    if len(lines) < 2 or lines[0] != "schema_version: 1":
        _fail("schema_version must be 1")
    if lines[1] != "documents:":
        _fail("inventory must contain a documents list")
    raw_entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for number, line in enumerate(lines[2:], start=3):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("  - "):
            if current is not None:
                raw_entries.append(current)
            current = {}
            field = line[4:]
        elif line.startswith("    ") and current is not None:
            field = line[4:]
        else:
            _fail(f"{INVENTORY_PATH}:{number}: invalid inventory syntax")
        if ": " not in field:
            _fail(f"{INVENTORY_PATH}:{number}: invalid inventory field")
        key, value = field.split(": ", 1)
        if key not in ENTRY_KEYS:
            _fail(f"{INVENTORY_PATH}:{number}: unknown inventory key: {key}")
        if key in current:
            _fail(f"{INVENTORY_PATH}:{number}: duplicate inventory key: {key}")
        if not value:
            _fail(f"{INVENTORY_PATH}:{number}: inventory value must not be empty")
        current[key] = value
    if current is not None:
        raw_entries.append(current)

    entries: list[DocumentEntry] = []
    for raw in raw_entries:
        missing = {"path", "lifecycle", "status"} - raw.keys()
        if missing:
            _fail(f"inventory entry is missing key(s): {', '.join(sorted(missing))}")
        path = PurePosixPath(raw["path"])
        entries.append(DocumentEntry(path, raw["lifecycle"], raw["status"], raw.get("index")))
    return tuple(entries)


def _validate_inventory(
    entries: tuple[DocumentEntry, ...], tracked: tuple[PurePosixPath, ...]
) -> None:
    paths = tuple(entry.path for entry in entries)
    duplicates = sorted(path for path, count in Counter(paths).items() if count > 1)
    if duplicates:
        _fail(f"duplicate inventory path: {duplicates[0]}")
    for entry in entries:
        value = str(entry.path)
        if entry.path.suffix not in {".md", ".mdx"}:
            _fail(f"inventory path must end in .md or .mdx: {value}")
        if (
            entry.path.is_absolute()
            or "\\" in value
            or "\0" in value
            or value in {".", ".."}
            or ".." in entry.path.parts
        ):
            _fail(f"inventory path must be safe repository-relative POSIX path: {value}")
        expected_status = LIFECYCLE_STATUS.get(entry.lifecycle)
        if expected_status is None or expected_status != entry.status:
            _fail(f"lifecycle/status mismatch: {value}")
        if entry.index is not None and entry.index not in INDEX_VALUES:
            _fail(f"invalid index value: {value}")
    if paths != tuple(sorted(paths, key=str)):
        _fail("inventory documents must be sorted by path")
    inventory_set = frozenset(paths)
    tracked_set = frozenset(tracked)
    if missing := sorted(tracked_set - inventory_set, key=str):
        _fail(f"inventory is missing tracked document: {missing[0]}")
    if extra := sorted(inventory_set - tracked_set, key=str):
        _fail(f"inventory contains untracked document: {extra[0]}")


def _read_documents(root: Path, tracked: tuple[PurePosixPath, ...]) -> dict[PurePosixPath, str]:
    texts: dict[PurePosixPath, str] = {}
    for relative in tracked:
        target = root.joinpath(*relative.parts)
        if _is_symlinked_path(root, relative):
            _fail(f"tracked document must be a regular UTF-8 file: {relative}")
        if not target.is_file():
            _fail(f"tracked document must be a regular UTF-8 file: {relative}")
        try:
            texts[relative] = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            _fail(f"tracked document must be a regular UTF-8 file: {relative}")
    return texts


def _is_symlinked_path(root: Path, relative: PurePosixPath) -> bool:
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _without_code(text: str) -> str:
    kept: list[str] = []
    fence: tuple[str, int] | None = None
    for line in text.splitlines():
        if fence is None and (opening := re.match(r"^ {0,3}(`{3,}|~{3,})", line)) is not None:
            marker = opening.group(1)
            fence = (marker[0], len(marker))
            continue
        if fence is not None:
            marker, minimum = fence
            if re.fullmatch(rf" {{0,3}}{re.escape(marker)}{{{minimum},}}\s*", line):
                fence = None
            continue
        kept.append(re.sub(r"(`+)[^`]*?\1", "", line))
    return "\n".join(kept)


def _heading_anchors(text: str) -> frozenset[str]:
    counts: Counter[str] = Counter()
    anchors: set[str] = set()
    for match in HEADING.finditer(_without_code(text)):
        heading = re.sub(r"<[^>]+>", "", match.group(1).strip()).lower()
        heading = re.sub(r"!?\[([^]]+)\]\([^)]+\)", r"\1", heading)
        heading = heading.replace("`", "").replace("*", "").replace("_", "_")
        slug = "".join(
            char
            for char in heading
            if (
                not unicodedata.category(char).startswith(("P", "S"))
                and ord(char) not in range(0xFE00, 0xFE10)
            )
            or char in {"-", "_"}
        ).replace(" ", "-")
        duplicate = counts[slug]
        counts[slug] += 1
        anchors.add(f"{slug}-{duplicate}" if duplicate else slug)
    return frozenset(anchors)


def _link_destination(raw: str) -> str:
    value = raw.strip()
    if value.startswith("<") and ">" in value:
        return value[1 : value.index(">")]
    return value.split(maxsplit=1)[0]


def _relative_target(source: PurePosixPath, destination: str) -> tuple[PurePosixPath, str]:
    split, decoded_path, decoded_fragment = _validated_destination(source, destination)
    if not decoded_path:
        return source, decoded_fragment
    parts = list(source.parent.parts)
    for part in PurePosixPath(decoded_path).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                _fail(f"link escapes repository: {source}")
            parts.pop()
        else:
            parts.append(part)
    return PurePosixPath(*parts), decoded_fragment


def _validated_destination(source: PurePosixPath, destination: str) -> tuple[SplitResult, str, str]:
    decoded_destination = unquote(destination)
    if "\\" in decoded_destination or any(
        ord(char) < 32 or ord(char) == 127 for char in decoded_destination
    ):
        _fail(f"unsafe link destination: {source}")
    try:
        split = urlsplit(decoded_destination)
    except ValueError:
        _fail(f"unsafe link destination: {source}")
    if split.scheme and split.scheme not in EXTERNAL_SCHEMES:
        _fail(f"unsupported link scheme: {source}")
    decoded_path = unquote(split.path)
    decoded_fragment = unquote(split.fragment)
    if not split.scheme and not destination.startswith("/") and decoded_path.startswith("/"):
        _fail(f"unsafe link destination: {source}")
    return split, decoded_path, decoded_fragment


def _validate_links(
    root: Path, texts: dict[PurePosixPath, str], tracked: frozenset[PurePosixPath]
) -> None:
    for source, text in texts.items():
        for raw in LINK.findall(_without_code(text)):
            destination = _link_destination(raw)
            split, _decoded_path, _decoded_fragment = _validated_destination(source, destination)
            # Root-relative links are product routes or historical local-path citations,
            # rather than portable repository-document links.
            if split.scheme in EXTERNAL_SCHEMES or destination.startswith("/"):
                continue
            target, fragment = _relative_target(source, destination)
            absolute = root.joinpath(*target.parts)
            directory_navigation = destination.split("#", 1)[0].endswith("/")
            if not absolute.exists():
                _fail(f"link target does not exist: {source} -> {target}")
            if not absolute.is_file() and not (directory_navigation and absolute.is_dir()):
                _fail(f"link target is not a regular file: {source} -> {target}")
            if absolute.is_file() and _is_symlinked_path(root, target):
                _fail(f"link target is not a regular file: {source} -> {target}")
            if fragment:
                target_text = texts.get(target)
                if target_text is None:
                    if target not in tracked:
                        _fail(f"fragment target is not tracked Markdown: {source} -> {target}")
                    target_text = absolute.read_text(encoding="utf-8")
                if fragment not in _heading_anchors(target_text):
                    _fail(f"fragment does not exist: {source} -> {target}#{fragment}")


def _resolved_index_links(index_path: PurePosixPath, text: str) -> tuple[PurePosixPath, ...]:
    values: list[PurePosixPath] = []
    for raw in LINK.findall(_without_code(text)):
        destination = _link_destination(raw)
        split, _decoded_path, _decoded_fragment = _validated_destination(index_path, destination)
        if split.scheme or destination.startswith("#"):
            continue
        target, _fragment = _relative_target(index_path, destination)
        values.append(target)
    return tuple(values)


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^##\s+{re.escape(heading)}\s*$", text, re.MULTILINE)
    if match is None:
        _fail(f"execution-plan index is missing ## {heading}")
    following = re.search(r"^##\s+", text[match.end() :], re.MULTILINE)
    end = match.end() + following.start() if following else len(text)
    return text[match.end() : end]


def _validate_indexes(entries: tuple[DocumentEntry, ...], texts: dict[PurePosixPath, str]) -> int:
    markers = {
        name: frozenset(entry.path for entry in entries if entry.index == name)
        for name in INDEX_VALUES
    }
    adr_files = frozenset(entry.path for entry in entries if ADR_PATH.fullmatch(str(entry.path)))
    if any(
        entry.lifecycle != "decision-record" or entry.index != "adr"
        for entry in entries
        if entry.path in adr_files
    ):
        _fail("numbered ADR files must be decision-record entries indexed as adr")
    if markers["adr"] != adr_files:
        _fail("only numbered ADR files may use index: adr")
    if any(
        entry.lifecycle != expected
        for entry in entries
        for marker, expected in (
            ("active-plan", "active-plan"),
            ("completed-plan", "completed-plan"),
        )
        if entry.index == marker
    ):
        _fail("execution-plan index markers must match document lifecycle")
    adr_index = PurePosixPath("docs/design-docs/index.md")
    rows = ADR_ROW.findall(texts[adr_index])
    actual_pairs = tuple(
        (number, target)
        for number, raw in rows
        for target, _fragment in [_relative_target(adr_index, _link_destination(raw))]
    )
    actual_adr = frozenset(target for _number, target in actual_pairs)
    numbers = tuple(number for number, _target in actual_pairs)
    numbers_match_paths = all(
        (match := ADR_PATH.fullmatch(str(target))) is not None and match.group(1) == number
        for number, target in actual_pairs
    )
    if (
        actual_adr != markers["adr"]
        or len(rows) != len(actual_adr)
        or len(numbers) != len(set(numbers))
        or not numbers_match_paths
    ):
        _fail("ADR index must exactly match indexed ADR documents")

    plan_index = PurePosixPath("docs/exec-plans/index.md")
    active_links = _resolved_index_links(plan_index, _section(texts[plan_index], "Active"))
    completed_links = _resolved_index_links(plan_index, _section(texts[plan_index], "Completed"))
    active = frozenset(active_links)
    completed = frozenset(completed_links)
    if active != markers["active-plan"] or len(active_links) != len(active):
        _fail("Active plan index must exactly match indexed active plans")
    if completed != markers["completed-plan"] or len(completed_links) != len(completed):
        _fail("Completed plan index must exactly match indexed completed plans")

    references_index = PurePosixPath("docs/references/index.md")
    reference_links = _resolved_index_links(references_index, texts[references_index])
    references = frozenset(reference_links)
    if references != markers["references"] or len(reference_links) != len(references):
        _fail("references index must exactly match indexed reference documents")
    return len(adr_files)


def inspect_documentation(root: Path) -> DocumentationSummary:
    inventory_file = root.joinpath(*INVENTORY_PATH.parts)
    try:
        inventory_text = inventory_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        _fail(f"missing or invalid inventory: {INVENTORY_PATH}")
    entries = _parse_inventory(inventory_text)
    tracked = _tracked_markdown(root)
    _validate_inventory(entries, tracked)
    texts = _read_documents(root, tracked)
    _validate_links(root, texts, frozenset(tracked))
    adr_count = _validate_indexes(entries, texts)
    return DocumentationSummary(len(entries), adr_count)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    arguments = parser.parse_args()
    try:
        summary = inspect_documentation(arguments.root)
    except DocsConsistencyError as error:
        print(error, file=sys.stderr)
        return 1
    print(
        f"documentation inventory OK: {summary.document_count} documents, {summary.adr_count} ADRs"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
