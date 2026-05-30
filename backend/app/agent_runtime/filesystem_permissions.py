"""DeepAgents filesystem permission policy for Moldy runtime paths."""

from __future__ import annotations

from deepagents.middleware.filesystem import FilesystemPermission


def _path_and_descendants(path: str) -> list[str]:
    normalized = "/" + path.strip("/")
    return [normalized, f"{normalized}/", f"{normalized}/**"]


def _protected_tree(path: str) -> FilesystemPermission:
    return FilesystemPermission(
        operations=["read", "write"],
        paths=_path_and_descendants(path),
        mode="deny",
    )


def build_filesystem_permissions(
    *,
    thread_id: str,
    agent_id: str | None,
    user_id: str | None,
    selected_skill_slugs: list[str],
) -> list[FilesystemPermission]:
    """Build ordered DeepAgents filesystem rules for one runtime invocation.

    DeepAgents evaluates permission rules in declaration order and allows
    unmatched paths. We allow the current scoped runtime surfaces first, then
    deny every protected shared tree so one agent cannot browse another
    agent's skills, memory, or conversation outputs through built-in file tools.
    """

    if agent_id and not user_id:
        raise ValueError("user_id is required when building agent-scoped filesystem permissions")

    permissions: list[FilesystemPermission] = []

    for slug in selected_skill_slugs:
        permissions.append(
            FilesystemPermission(
                operations=["read"],
                paths=_path_and_descendants(f"/runtime/{thread_id}/skills/{slug}"),
                mode="allow",
            )
        )

    permissions.append(
        FilesystemPermission(
            operations=["read", "write"],
            paths=_path_and_descendants(f"/conversations/{thread_id}"),
            mode="allow",
        )
    )

    if agent_id:
        permissions.append(
            FilesystemPermission(
                operations=["read", "write"],
                paths=[f"/agents/{agent_id}/AGENTS.md"],
                mode="allow",
            )
        )

    permissions.extend(
        [
            _protected_tree("/skills"),
            _protected_tree("/agents"),
            _protected_tree("/runtime"),
            _protected_tree("/conversations"),
        ]
    )
    return permissions


__all__ = ["build_filesystem_permissions"]
