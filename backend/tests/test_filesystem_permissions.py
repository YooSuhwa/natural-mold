from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from deepagents.backends import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemMiddleware

from app.agent_runtime.runtime_config import AgentConfig
from tests.tool_helpers import tool_coroutine


def _cfg(**overrides: object) -> AgentConfig:
    defaults: dict[str, object] = {
        "provider": "openai",
        "model_name": "gpt-4o",
        "api_key": None,
        "base_url": None,
        "system_prompt": "Hi",
        "tools_config": [],
        "thread_id": "thread-a",
        "agent_id": "agent-a",
        "user_id": "00000000-0000-0000-0000-000000000001",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)  # type: ignore[arg-type]


def _tool_by_name(middleware: FilesystemMiddleware, name: str):
    for tool in middleware.tools:
        if tool.name == name:
            return tool
    raise AssertionError(f"tool not found: {name}")


def _seed_virtual_data(root: Path) -> None:
    for path, body in {
        "runtime/thread-a/skills/selected/SKILL.md": "# selected\n",
        "runtime/thread-a/skills/stale-unselected/SKILL.md": "# stale\n",
        "runtime/thread-b/skills/selected/SKILL.md": "# other thread\n",
        "skills/canonical/SKILL.md": "# canonical\n",
        "agents/agent-a/AGENTS.md": "# own memory\n",
        "agents/agent-b/AGENTS.md": "# other memory\n",
        "conversations/thread-a/output.txt": "own output\n",
        "conversations/thread-b/output.txt": "other output\n",
    }.items():
        file_path = root / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(body)


def test_scoped_filesystem_permissions_only_allow_current_runtime_surfaces() -> None:
    from deepagents.middleware.filesystem import _check_fs_permission

    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions

    permissions = build_filesystem_permissions(
        thread_id="thread-a",
        agent_id="agent-a",
        user_id="user-a",
        selected_skill_slugs=["selected"],
    )

    assert (
        _check_fs_permission(
            permissions,
            "read",
            "/runtime/thread-a/skills/selected/SKILL.md",
        )
        == "allow"
    )
    assert (
        _check_fs_permission(
            permissions,
            "write",
            "/runtime/thread-a/skills/selected/SKILL.md",
        )
        == "deny"
    )
    assert _check_fs_permission(permissions, "read", "/skills/canonical/SKILL.md") == "deny"
    assert (
        _check_fs_permission(
            permissions,
            "read",
            "/runtime/thread-a/skills/stale-unselected/SKILL.md",
        )
        == "deny"
    )
    assert (
        _check_fs_permission(
            permissions,
            "read",
            "/runtime/thread-b/skills/selected/SKILL.md",
        )
        == "deny"
    )
    assert _check_fs_permission(permissions, "read", "/agents/agent-a/AGENTS.md") == "allow"
    assert _check_fs_permission(permissions, "write", "/agents/agent-a/AGENTS.md") == "allow"
    assert _check_fs_permission(permissions, "read", "/agents/agent-b/AGENTS.md") == "deny"
    assert (
        _check_fs_permission(permissions, "read", "/conversations/thread-a/output.txt") == "allow"
    )
    assert _check_fs_permission(permissions, "write", "/conversations/thread-a/new.txt") == "allow"
    assert _check_fs_permission(permissions, "read", "/conversations/thread-b/output.txt") == "deny"
    assert _check_fs_permission(permissions, "write", "/tmp/invisible.txt") == "deny"
    # data/uploads = 모든 사용자의 첨부 blob — 기본-allow 구멍 봉쇄 (스펙 §6-2).
    assert _check_fs_permission(permissions, "read", "/uploads/deadbeef.png") == "deny"
    assert _check_fs_permission(permissions, "read", "/uploads") == "deny"
    assert _check_fs_permission(permissions, "write", "/uploads/deadbeef.png") == "deny"


def test_draft_workspace_mount_allows_session_and_denies_siblings() -> None:
    """스킬 빌더 드래프트 마운트 (스펙 AD-2): 세션 allow → sibling deny 순서."""

    from deepagents.middleware.filesystem import _check_fs_permission

    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions

    permissions = build_filesystem_permissions(
        thread_id="thread-a",
        agent_id="agent-a",
        user_id="user-a",
        selected_skill_slugs=[],
        draft_workspace_path="skill-drafts/session-1",
    )

    # 자기 세션 워크스페이스는 read+write 가능 (write_file/edit_file 편집 표면).
    assert _check_fs_permission(permissions, "read", "/skill-drafts/session-1/SKILL.md") == "allow"
    assert _check_fs_permission(permissions, "write", "/skill-drafts/session-1/SKILL.md") == "allow"
    assert (
        _check_fs_permission(permissions, "write", "/skill-drafts/session-1/references/a.md")
        == "allow"
    )
    # 타 세션 워크스페이스는 완전 차단 (sibling deny).
    assert _check_fs_permission(permissions, "read", "/skill-drafts/session-2/SKILL.md") == "deny"
    assert _check_fs_permission(permissions, "write", "/skill-drafts/session-2/SKILL.md") == "deny"
    assert _check_fs_permission(permissions, "read", "/skill-drafts") == "deny"


def test_no_draft_mount_denies_entire_skill_drafts_tree() -> None:
    """드래프트 마운트가 없는 일반 런에서도 /skill-drafts 전체가 deny."""

    from deepagents.middleware.filesystem import _check_fs_permission

    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions

    permissions = build_filesystem_permissions(
        thread_id="thread-a",
        agent_id="agent-a",
        user_id="user-a",
        selected_skill_slugs=[],
    )

    assert _check_fs_permission(permissions, "read", "/skill-drafts/session-1/SKILL.md") == "deny"
    assert _check_fs_permission(permissions, "write", "/skill-drafts/session-1/SKILL.md") == "deny"


def test_filesystem_permissions_can_scope_skills_by_agent_runtime_name() -> None:
    from deepagents.middleware.filesystem import _check_fs_permission

    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions

    permissions = build_filesystem_permissions(
        thread_id="thread-a",
        agent_id="agent-a",
        user_id="user-a",
        selected_skill_slugs=["selected"],
        agent_runtime_name="agent_1234abcd",
    )

    assert (
        _check_fs_permission(
            permissions,
            "read",
            "/runtime/thread-a/agents/agent_1234abcd/skills/selected/SKILL.md",
        )
        == "allow"
    )
    assert (
        _check_fs_permission(
            permissions,
            "read",
            "/runtime/thread-a/skills/selected/SKILL.md",
        )
        == "deny"
    )


@pytest.mark.asyncio
async def test_deepagents_filesystem_tools_enforce_scoped_permissions(
    tmp_path: Path,
) -> None:
    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions

    _seed_virtual_data(tmp_path)
    permissions = build_filesystem_permissions(
        thread_id="thread-a",
        agent_id="agent-a",
        user_id="user-a",
        selected_skill_slugs=["selected"],
    )
    middleware = FilesystemMiddleware(
        backend=FilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        _permissions=permissions,
    )
    runtime = SimpleNamespace(tool_call_id="call-1")
    ls = _tool_by_name(middleware, "ls")
    read_file = _tool_by_name(middleware, "read_file")
    write_file = _tool_by_name(middleware, "write_file")
    edit_file = _tool_by_name(middleware, "edit_file")
    ls_run = tool_coroutine(ls)
    read_file_run = tool_coroutine(read_file)
    write_file_run = tool_coroutine(write_file)
    edit_file_run = tool_coroutine(edit_file)

    root_listing = await ls_run(path="/", runtime=runtime)
    assert "/agents/" not in root_listing.content
    assert "/runtime/" not in root_listing.content
    assert "/skills/" not in root_listing.content
    assert "/conversations/" not in root_listing.content

    selected_skill = await read_file_run(
        file_path="/runtime/thread-a/skills/selected/SKILL.md",
        runtime=runtime,
    )
    assert selected_skill.status == "success"
    assert "selected" in selected_skill.content

    other_thread_skill = await read_file_run(
        file_path="/runtime/thread-b/skills/selected/SKILL.md",
        runtime=runtime,
    )
    assert other_thread_skill.status == "error"
    assert "permission denied" in other_thread_skill.content

    canonical_skill = await read_file_run(
        file_path="/skills/canonical/SKILL.md",
        runtime=runtime,
    )
    assert canonical_skill.status == "error"
    assert "permission denied" in canonical_skill.content

    stale_unselected_skill = await read_file_run(
        file_path="/runtime/thread-a/skills/stale-unselected/SKILL.md",
        runtime=runtime,
    )
    assert stale_unselected_skill.status == "error"
    assert "permission denied" in stale_unselected_skill.content

    own_memory = await read_file_run(
        file_path="/agents/agent-a/AGENTS.md",
        runtime=runtime,
    )
    assert own_memory.status == "success"
    assert "own memory" in own_memory.content

    other_memory = await read_file_run(
        file_path="/agents/agent-b/AGENTS.md",
        runtime=runtime,
    )
    assert other_memory.status == "error"
    assert "permission denied" in other_memory.content

    write_allowed = await write_file_run(
        file_path="/conversations/thread-a/new.txt",
        content="new output\n",
        runtime=runtime,
    )
    assert write_allowed.status == "success"
    assert (tmp_path / "conversations" / "thread-a" / "new.txt").read_text() == "new output\n"

    write_denied = await write_file_run(
        file_path="/conversations/thread-b/new.txt",
        content="other output\n",
        runtime=runtime,
    )
    assert write_denied.status == "error"
    assert "permission denied" in write_denied.content

    untracked_write_denied = await write_file_run(
        file_path="/tmp/invisible.txt",
        content="not an artifact\n",
        runtime=runtime,
    )
    assert untracked_write_denied.status == "error"
    assert "permission denied" in untracked_write_denied.content
    assert not (tmp_path / "tmp" / "invisible.txt").exists()

    edit_denied = await edit_file_run(
        file_path="/agents/agent-b/AGENTS.md",
        old_string="# other memory\n",
        new_string="# tampered\n",
        runtime=runtime,
    )
    assert edit_denied.status == "error"
    assert "permission denied" in edit_denied.content
    assert (tmp_path / "agents" / "agent-b" / "AGENTS.md").read_text() == "# other memory\n"


@pytest.mark.asyncio
@patch("app.agent_runtime.runtime_component_builder.FilesystemBackend")
@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.agent_stream_runner.stream_agent_response")
@patch("app.agent_runtime.runtime_component_builder.build_agent")
@patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages")
@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
async def test_prepare_agent_passes_scoped_permissions_to_deepagents(
    mock_model_factory: MagicMock,
    mock_convert: MagicMock,
    mock_build: MagicMock,
    mock_stream: MagicMock,
    mock_checkpointer: MagicMock,
    mock_fs_backend_cls: MagicMock,
    tmp_path: Path,
) -> None:
    from app.agent_runtime.agent_stream_runner import execute_agent_stream
    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions

    mock_model_factory.return_value = MagicMock()
    mock_convert.return_value = []
    mock_build.return_value = MagicMock()

    async def fake_stream(*args, **kwargs):
        yield "done"

    mock_stream.return_value = fake_stream()

    mock_data_dir = tmp_path / "data"
    mock_data_dir.mkdir()
    skill_src = tmp_path / "selected-skill"
    skill_src.mkdir()
    (skill_src / "SKILL.md").write_text("# selected\n")
    agent_skills = [
        {
            "id": str(uuid.uuid4()),
            "slug": "selected",
            "name": "Selected",
            "kind": "package",
            "storage_path": str(skill_src),
            "description": "",
        }
    ]

    with (
        patch("app.agent_runtime.runtime_component_builder._DATA_DIR", mock_data_dir),
        patch(
            "app.agent_runtime.runtime_component_builder.resolve_runtime_credentials",
            new_callable=AsyncMock,
        ),
    ):
        async for _ in execute_agent_stream(_cfg(agent_skills=agent_skills), []):
            pass

    build_kwargs = mock_build.call_args[1]
    assert build_kwargs["permissions"] == build_filesystem_permissions(
        thread_id="thread-a",
        agent_id="agent-a",
        user_id="00000000-0000-0000-0000-000000000001",
        selected_skill_slugs=["selected"],
    )


def test_agent_scoped_filesystem_permissions_require_user_identity() -> None:
    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions

    with pytest.raises(ValueError, match="user_id is required"):
        build_filesystem_permissions(
            thread_id="thread-a",
            agent_id="agent-a",
            user_id=None,
            selected_skill_slugs=[],
        )


def test_builder_run_denies_conversation_tree_writes() -> None:
    """R2 회귀: 드래프트 마운트 런은 /conversations 쓰기 권한을 받지 않는다 —
    부여하면 히든 빌더 에이전트의 산출물이 아티팩트로 인덱싱되어 라이브러리에
    노출될 수 있다. 일반 런의 conversation allow는 그대로 유지."""

    from deepagents.middleware.filesystem import _check_fs_permission

    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions

    builder = build_filesystem_permissions(
        thread_id="thread-b",
        agent_id="agent-b",
        user_id="user-b",
        selected_skill_slugs=[],
        draft_workspace_path="skill-drafts/session-9",
    )
    assert _check_fs_permission(builder, "write", "/conversations/thread-b/out.md") == "deny"
    assert _check_fs_permission(builder, "read", "/conversations/thread-b/out.md") == "deny"

    standard = build_filesystem_permissions(
        thread_id="thread-b",
        agent_id="agent-b",
        user_id="user-b",
        selected_skill_slugs=[],
    )
    assert _check_fs_permission(standard, "write", "/conversations/thread-b/out.md") == "allow"


def test_malformed_draft_workspace_path_fails_closed() -> None:
    """R2 회귀: strip 후 빈 경로나 skill-drafts/ 밖 경로는 ValueError —
    빈 문자열이 통과하면 `/**` allow가 전체 FS를 연다 (불변식 가드)."""

    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions

    # 빈 문자열은 falsy라 마운트 자체가 생략된다(안전) — raise 대상 아님.
    for bad in ("/", "uploads/evil", "skill-drafts"):
        with pytest.raises(ValueError, match="must live under skill-drafts/"):
            build_filesystem_permissions(
                thread_id="thread-c",
                agent_id="agent-c",
                user_id="user-c",
                selected_skill_slugs=[],
                draft_workspace_path=bad,
            )
