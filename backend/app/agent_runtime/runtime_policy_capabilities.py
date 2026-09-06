"""Source-gated filesystem and subagent capabilities for stored runtime policies."""

from __future__ import annotations

from typing import Final, Literal, assert_never

from deepagents.middleware.filesystem import FilesystemPermission

from app.agent_runtime.filesystem_permissions import (
    FilesystemOperation,
    _path_and_descendants,
    _validate_rule_segment,
)
from app.agent_runtime.runtime_policy_parent_permissions import (
    attest_stored_filesystem_permissions,
    canonical_denies,
    deny_all_filesystem_permissions,
)

STORED_RESERVED_TOOL_NAMES: Final = frozenset(
    {
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "delete",
        "glob",
        "grep",
        "execute",
        "execute_in_skill",
        "shell",
        "task",
        "write_todos",
    }
)


class RestrictedSubagentSpecError(Exception):
    """A stored-policy subagent spec requests an unattested execution unit."""

    code = "RESTRICTED_SUBAGENT_SPEC_UNSUPPORTED"

    def __str__(self) -> str:
        return self.code


def build_stored_filesystem_permissions(
    *,
    thread_id: str,
    agent_id: str | None,
    user_id: str | None,
    selected_skill_slugs: list[str],
    agent_runtime_name: str | None,
    include_agent_memory_file: bool,
    mode: Literal["inspect", "artifact_write"],
) -> list[FilesystemPermission]:
    """Build the fail-closed filesystem boundary for an explicit stored policy."""

    if agent_id and not user_id:
        raise MissingFilesystemIdentityError
    _validate_rule_segment(thread_id)
    if agent_id:
        _validate_rule_segment(agent_id)
    if agent_runtime_name:
        _validate_rule_segment(agent_runtime_name)
    for slug in selected_skill_slugs:
        _validate_rule_segment(slug)

    skill_base = (
        f"/runtime/{thread_id}/agents/{agent_runtime_name}/skills"
        if agent_runtime_name
        else f"/runtime/{thread_id}/skills"
    )
    permissions = [
        FilesystemPermission(
            operations=["read"],
            paths=_path_and_descendants(f"{skill_base}/{slug}"),
            mode="allow",
        )
        for slug in selected_skill_slugs
    ]
    conversation_operations: list[FilesystemOperation] = ["read"]
    match mode:
        case "inspect":
            pass
        case "artifact_write":
            conversation_operations.append("write")
        case unreachable:
            assert_never(unreachable)
    permissions.append(
        FilesystemPermission(
            operations=conversation_operations,
            paths=_path_and_descendants(f"/conversations/{thread_id}"),
            mode="allow",
        )
    )
    if include_agent_memory_file and agent_id:
        permissions.append(
            FilesystemPermission(
                operations=["read"],
                paths=[f"/agents/{agent_id}/AGENTS.md"],
                mode="allow",
            )
        )
    permissions.extend(canonical_denies())
    return attest_stored_filesystem_permissions(permissions, mode=mode)


def sanitize_child_filesystem_permissions(
    parent_permissions: list[FilesystemPermission] | None,
    candidate_permissions: list[FilesystemPermission] | None,
    *,
    child_name: str | None,
    trusted_child: bool,
    allow_child_writes: bool,
) -> list[FilesystemPermission]:
    """Keep child paths actor-scoped while capping operations to the parent mode."""

    if not parent_permissions or not any(
        rule.mode == "deny" and set(rule.operations) == {"read", "write"} and rule.paths == ["/**"]
        for rule in parent_permissions
    ):
        return deny_all_filesystem_permissions()
    conversation_rule = next(
        (
            rule
            for rule in parent_permissions
            if rule.mode == "allow"
            and "read" in rule.operations
            and len(rule.paths) == 3
            and rule.paths[0].startswith("/conversations/")
            and rule.paths == _path_and_descendants(rule.paths[0])
        ),
        None,
    )
    if conversation_rule is None:
        return deny_all_filesystem_permissions()
    conversation_root = conversation_rule.paths[0]
    thread_id = conversation_root.removeprefix("/conversations/")
    try:
        _validate_rule_segment(thread_id)
    except ValueError:
        return deny_all_filesystem_permissions()
    runtime_root = f"/runtime/{thread_id}/"
    if trusted_child:
        if not child_name:
            return deny_all_filesystem_permissions()
        try:
            _validate_rule_segment(child_name)
        except ValueError:
            return deny_all_filesystem_permissions()
    child_skill_root = (
        f"{runtime_root}agents/{child_name}/skills/"
        if trusted_child and child_name
        else runtime_root
    )
    sanitized: list[FilesystemPermission] = []
    for rule in candidate_permissions or ():
        if rule.mode != "allow" or "read" not in rule.operations or len(rule.paths) != 3:
            continue
        skill_root = rule.paths[0]
        if rule.paths != _path_and_descendants(skill_root):
            continue
        if not skill_root.startswith(child_skill_root) or "/skills/" not in skill_root:
            continue
        slug = skill_root.rsplit("/", maxsplit=1)[-1]
        try:
            _validate_rule_segment(slug)
        except ValueError:
            continue
        sanitized.append(
            FilesystemPermission(
                operations=["read"],
                paths=list(rule.paths),
                mode="allow",
            )
        )
    conversation_operations: list[FilesystemOperation] = ["read"]
    if allow_child_writes and "write" in conversation_rule.operations:
        conversation_operations.append("write")
    sanitized.append(
        FilesystemPermission(
            operations=conversation_operations,
            paths=_path_and_descendants(conversation_root),
            mode="allow",
        )
    )
    sanitized.extend(canonical_denies())
    return sanitized


class MissingFilesystemIdentityError(ValueError):
    """A stored agent filesystem scope has no authenticated owner identity."""

    def __str__(self) -> str:
        return "user_id is required when building agent-scoped filesystem permissions"
