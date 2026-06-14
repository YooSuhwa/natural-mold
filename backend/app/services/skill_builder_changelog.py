from __future__ import annotations

from dataclasses import dataclass

from app.models.skill_builder_session import JsonValue
from app.schemas.skill_builder import SkillBuilderMode, SkillDraftPackage


@dataclass(frozen=True, slots=True)
class RevisionChangelog:
    summary: str | None
    items: list[JsonValue] | None


def build_revision_changelog(
    *,
    mode: SkillBuilderMode,
    base_snapshot: dict[str, JsonValue] | None,
    draft: SkillDraftPackage,
    provided: dict[str, JsonValue] | None,
) -> RevisionChangelog:
    summary = _provided_summary(provided)
    if mode is SkillBuilderMode.CREATE:
        items = _create_items(draft)
        return RevisionChangelog(
            summary=summary or _create_summary(len(items)),
            items=items,
        )
    base_files = _file_map_from_raw(base_snapshot)
    draft_files = {file.path: file.content for file in draft.files}
    items = _diff_items(base_files, draft_files)
    return RevisionChangelog(
        summary=summary or _diff_summary(items),
        items=items,
    )


def _provided_summary(provided: dict[str, JsonValue] | None) -> str | None:
    if not provided:
        return None
    summary = provided.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    return None


def _create_items(draft: SkillDraftPackage) -> list[JsonValue]:
    return [
        {"operation": "added", "path": path} for path in sorted(file.path for file in draft.files)
    ]


def _file_map_from_raw(raw: dict[str, JsonValue] | None) -> dict[str, str]:
    if raw is None:
        return {}
    files = raw.get("files")
    if not isinstance(files, list):
        return {}
    result: dict[str, str] = {}
    for item in files:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        content = item.get("content")
        if isinstance(path, str) and isinstance(content, str):
            result[path] = content
    return result


def _diff_items(base_files: dict[str, str], draft_files: dict[str, str]) -> list[JsonValue]:
    added = draft_files.keys() - base_files.keys()
    deleted = base_files.keys() - draft_files.keys()
    modified = {
        path
        for path in draft_files.keys() & base_files.keys()
        if draft_files[path] != base_files[path]
    }
    return [
        *[{"operation": "added", "path": path} for path in sorted(added)],
        *[{"operation": "modified", "path": path} for path in sorted(modified)],
        *[{"operation": "deleted", "path": path} for path in sorted(deleted)],
    ]


def _create_summary(file_count: int) -> str:
    return f"Created skill package with {file_count} {_plural(file_count)}."


def _diff_summary(items: list[JsonValue]) -> str:
    counts = {"added": 0, "modified": 0, "deleted": 0}
    for item in items:
        if not isinstance(item, dict):
            continue
        operation = item.get("operation")
        if operation in counts:
            counts[operation] += 1
    if not any(counts.values()):
        return "Changed skill package without file changes."
    segments = []
    if counts["added"]:
        segments.append(f"added {counts['added']} {_plural(counts['added'])}")
    if counts["modified"]:
        segments.append(f"modified {counts['modified']} {_plural(counts['modified'])}")
    if counts["deleted"]:
        segments.append(f"removed {counts['deleted']} {_plural(counts['deleted'])}")
    return f"Changed skill package: {', '.join(segments)}."


def _plural(count: int) -> str:
    return "file" if count == 1 else "files"
