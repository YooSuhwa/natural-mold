"""Attestation and canonical reconstruction for stored parent filesystem permissions."""

from __future__ import annotations

from typing import Literal

from deepagents.middleware.filesystem import FilesystemPermission

from app.agent_runtime.filesystem_permissions import (
    FilesystemOperation,
    _path_and_descendants,
    _protected_tree,
    _validate_rule_segment,
)

type FilesystemMode = Literal["inspect", "artifact_write"]
type PermissionFingerprint = tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...]

_PROTECTED_RUNTIME_TREES = (
    "/skills",
    "/agents",
    "/runtime",
    "/conversations",
    "/skill-drafts",
    "/uploads",
    "/artifacts",
    "/.moldy-offload",
)


def _permission_fingerprint(permissions: list[FilesystemPermission]) -> PermissionFingerprint:
    return tuple((tuple(rule.operations), tuple(rule.paths), rule.mode) for rule in permissions)


class _StoredFilesystemPermissions(list[FilesystemPermission]):
    """Mutable compatibility list whose original canonical contents remain attestable."""

    def __init__(self, permissions: list[FilesystemPermission], mode: FilesystemMode) -> None:
        super().__init__(permissions)
        self._attested_mode = mode
        self._attested_fingerprint = _permission_fingerprint(permissions)

    def is_attested(self, mode: FilesystemMode) -> bool:
        return (
            self._attested_mode == mode
            and self._attested_fingerprint == _permission_fingerprint(self)
        )


def attest_stored_filesystem_permissions(
    permissions: list[FilesystemPermission],
    *,
    mode: FilesystemMode,
) -> list[FilesystemPermission]:
    """Tag fresh builder output so copies or later mutation fail closed at graph build."""

    return _StoredFilesystemPermissions(permissions, mode)


def deny_all_filesystem_permissions() -> list[FilesystemPermission]:
    """Return an explicit rule because Deep Agents treats missing rules as allow."""

    return [
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/**"],
            mode="deny",
        )
    ]


def canonical_denies() -> list[FilesystemPermission]:
    return [
        *(_protected_tree(path) for path in _PROTECTED_RUNTIME_TREES),
        *deny_all_filesystem_permissions(),
    ]


def canonicalize_stored_parent_permissions(
    permissions: list[FilesystemPermission] | None,
    *,
    mode: FilesystemMode,
) -> list[FilesystemPermission]:
    """Return a fresh canonical stored-policy list or an explicit deny-all fallback."""

    if not isinstance(permissions, _StoredFilesystemPermissions) or not permissions.is_attested(
        mode
    ):
        return deny_all_filesystem_permissions()
    denies = canonical_denies()
    if len(permissions) <= len(denies) or permissions[-len(denies) :] != denies:
        return deny_all_filesystem_permissions()
    allow_rules = permissions[: -len(denies)]
    conversation_rules = [
        rule
        for rule in allow_rules
        if rule.mode == "allow"
        and len(rule.paths) == 3
        and rule.paths[0].startswith("/conversations/")
        and rule.paths == _path_and_descendants(rule.paths[0])
    ]
    if len(conversation_rules) != 1:
        return deny_all_filesystem_permissions()
    conversation_rule = conversation_rules[0]
    thread_id = conversation_rule.paths[0].removeprefix("/conversations/")
    expected_operations: list[FilesystemOperation] = ["read"]
    if mode == "artifact_write":
        expected_operations.append("write")
    try:
        _validate_rule_segment(thread_id)
    except ValueError:
        return deny_all_filesystem_permissions()
    if conversation_rule.operations != expected_operations:
        return deny_all_filesystem_permissions()

    canonical_allows: list[FilesystemPermission] = []
    memory_rule_count = 0
    for rule in allow_rules:
        if rule is conversation_rule:
            canonical_allows.append(
                FilesystemPermission(
                    operations=list(expected_operations),
                    paths=_path_and_descendants(f"/conversations/{thread_id}"),
                    mode="allow",
                )
            )
            continue
        if rule.mode != "allow" or rule.operations != ["read"]:
            return deny_all_filesystem_permissions()
        parts = rule.paths[0].strip("/").split("/") if rule.paths else []
        if len(rule.paths) == 1 and len(parts) == 3 and parts[0] == "agents":
            if parts[2] != "AGENTS.md":
                return deny_all_filesystem_permissions()
            memory_rule_count += 1
            identifiers = [parts[1]]
        elif len(rule.paths) == 3 and rule.paths == _path_and_descendants(rule.paths[0]):
            valid_skill = (
                len(parts) == 4
                and parts[:3] == ["runtime", thread_id, "skills"]
                or len(parts) == 6
                and parts[:3] == ["runtime", thread_id, "agents"]
                and parts[4] == "skills"
            )
            if not valid_skill:
                return deny_all_filesystem_permissions()
            identifiers = [parts[-1], *(parts[3:4] if len(parts) == 6 else [])]
        else:
            return deny_all_filesystem_permissions()
        try:
            for identifier in identifiers:
                _validate_rule_segment(identifier)
        except ValueError:
            return deny_all_filesystem_permissions()
        if memory_rule_count > 1:
            return deny_all_filesystem_permissions()
        canonical_allows.append(
            FilesystemPermission(operations=["read"], paths=list(rule.paths), mode="allow")
        )
    return [*canonical_allows, *denies]
