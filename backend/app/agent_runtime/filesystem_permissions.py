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
    agent_runtime_name: str | None = None,
    draft_workspace_path: str | None = None,
) -> list[FilesystemPermission]:
    """Build ordered DeepAgents filesystem rules for one runtime invocation.

    DeepAgents evaluates permission rules in declaration order and allows
    unmatched paths. We allow the current scoped runtime surfaces first, then
    deny every protected shared tree so one agent cannot browse another
    agent's skills, memory, or conversation outputs through built-in file tools.

    ``draft_workspace_path``: 스킬 빌더 세션의 쓰기 가능 드래프트 마운트
    (ADR-018 상대경로, 예: ``skill-drafts/<session_id>``). allow 규칙이
    ``/skill-drafts/**`` deny **앞에** 와야 한다 — first-match-wins라 순서가
    곧 보안이다 (스펙 AD-2/§6-1).
    """

    if agent_id and not user_id:
        raise ValueError("user_id is required when building agent-scoped filesystem permissions")

    permissions: list[FilesystemPermission] = []
    skill_base = (
        f"/runtime/{thread_id}/agents/{agent_runtime_name}/skills"
        if agent_runtime_name
        else f"/runtime/{thread_id}/skills"
    )

    for slug in selected_skill_slugs:
        permissions.append(
            FilesystemPermission(
                operations=["read"],
                paths=_path_and_descendants(f"{skill_base}/{slug}"),
                mode="allow",
            )
        )

    # 빌더(드래프트 마운트) 런은 conversation 트리에 쓸 이유가 없다 — 부여하면
    # user-visible 파일이 아티팩트로 인덱싱되어 히든 에이전트 이름이 라이브러리에
    # 노출될 수 있다. 드래프트 워크스페이스가 유일한 쓰기 표면이다.
    if not draft_workspace_path:
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

    if draft_workspace_path:
        stripped = draft_workspace_path.strip("/")
        # 불변식 가드: 빈 문자열이면 _path_and_descendants가 "/**" allow를
        # 만들어 first-match-wins로 전체 FS가 열린다. 서버 생성 경로라 위반은
        # 버그이므로 fail-closed.
        if not stripped or not stripped.startswith("skill-drafts/"):
            raise ValueError(
                f"draft_workspace_path must live under skill-drafts/: {draft_workspace_path!r}"
            )
        permissions.append(
            FilesystemPermission(
                operations=["read", "write"],
                paths=_path_and_descendants(f"/{stripped}"),
                mode="allow",
            )
        )

    permissions.extend(
        [
            _protected_tree("/skills"),
            _protected_tree("/agents"),
            _protected_tree("/runtime"),
            _protected_tree("/conversations"),
            # 타 세션 드래프트 워크스페이스 차단 — unmatched 기본이 allow라 필수.
            _protected_tree("/skill-drafts"),
            # data/uploads holds every user's chat attachment blobs. Unmatched
            # paths default to allow, so without this rule any agent could
            # ``ls``/``read_file`` other users' uploads (cross-user exposure).
            _protected_tree("/uploads"),
            FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
        ]
    )
    return permissions


__all__ = ["build_filesystem_permissions"]
