from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from deepagents.backends import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from langchain_core.messages import ToolCall

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
async def test_moldy_compat_filesystem_middleware_keeps_scope_and_allows_overwrite(
    tmp_path: Path,
) -> None:
    """The 0.7 compatibility middleware is non-deleting and permission-scoped."""

    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions
    from app.agent_runtime.offload_storage_backends import HiddenFilesystemBackend
    from app.agent_runtime.runtime_component_builder import (
        _MOLDY_FILESYSTEM_TOOL_NAMES,
        _build_moldy_filesystem_middleware,
    )

    _seed_virtual_data(tmp_path)
    permissions = build_filesystem_permissions(
        thread_id="thread-a",
        agent_id="agent-a",
        user_id="user-a",
        selected_skill_slugs=["selected"],
    )
    backend = HiddenFilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    middleware = _build_moldy_filesystem_middleware(
        backend=backend,
        permissions=permissions,
    )
    assert tuple(tool.name for tool in middleware.tools) == _MOLDY_FILESYSTEM_TOOL_NAMES
    assert "delete" not in {tool.name for tool in middleware.tools}
    assert middleware.backend is backend
    assert middleware._permissions == permissions

    runtime = SimpleNamespace(tool_call_id="call-compat")
    read_file_run = tool_coroutine(_tool_by_name(middleware, "read_file"))
    write_file_run = tool_coroutine(_tool_by_name(middleware, "write_file"))

    denied = await read_file_run(
        file_path="/runtime/thread-b/skills/selected/SKILL.md",
        runtime=runtime,
    )
    assert denied.status == "error"
    assert "permission denied" in denied.content

    first_write = await write_file_run(
        file_path="/conversations/thread-a/overwrite.txt",
        content="first\n",
        runtime=runtime,
    )
    assert first_write.status == "success"
    overwrite = await write_file_run(
        file_path="/conversations/thread-a/overwrite.txt",
        content="second\n",
        runtime=runtime,
    )
    assert overwrite.status == "success"
    assert (tmp_path / "conversations" / "thread-a" / "overwrite.txt").read_text() == "second\n"


@pytest.mark.asyncio
async def test_moldy_filesystem_tools_enforce_scoped_permissions(
    tmp_path: Path,
) -> None:
    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions
    from app.agent_runtime.offload_storage_backends import HiddenFilesystemBackend
    from app.agent_runtime.runtime_component_builder import _build_moldy_filesystem_middleware

    _seed_virtual_data(tmp_path)
    permissions = build_filesystem_permissions(
        thread_id="thread-a",
        agent_id="agent-a",
        user_id="user-a",
        selected_skill_slugs=["selected"],
    )
    middleware = _build_moldy_filesystem_middleware(
        backend=HiddenFilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        permissions=permissions,
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
    assert root_listing.status == "error"
    assert root_listing.content == "Error: filesystem permission denied"

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


@pytest.mark.parametrize(
    "raw_path",
    [
        "relative/file.txt",
        "/conversations/thread-a/../thread-b/secret.txt",
        "/conversations/thread-a\\secret.txt",
        "/conversations/thread-a/%2fsecret.txt",
        "/conversations/thread-a/%252fsecret.txt",
        "/conversations/thread-a//secret.txt",
        "/conversations/thread-a/%",
        "/conversations/thread-a/%ZZ",
        "/conversations/thread-a/%2G",
    ],
)
def test_permission_calculator_rejects_ambiguous_path_forms(raw_path: str) -> None:
    from app.agent_runtime.filesystem_permissions import (
        UnsafeFilesystemPath,
        build_filesystem_permissions,
        calculate_filesystem_access,
    )

    permissions = build_filesystem_permissions(
        thread_id="thread-a",
        agent_id="agent-a",
        user_id="user-a",
        selected_skill_slugs=[],
    )

    with pytest.raises(UnsafeFilesystemPath):
        calculate_filesystem_access(permissions, "read", raw_path)


def test_permission_calculator_denies_unknown_and_prefix_collision() -> None:
    from app.agent_runtime.filesystem_permissions import (
        build_filesystem_permissions,
        calculate_filesystem_access,
    )

    permissions = build_filesystem_permissions(
        thread_id="thread-a",
        agent_id="agent-a",
        user_id="user-a",
        selected_skill_slugs=[],
    )

    assert calculate_filesystem_access(permissions, "read", "/unknown/file.txt")[0] == "deny"
    assert (
        calculate_filesystem_access(
            permissions,
            "read",
            "/conversations/thread-a-collision/file.txt",
        )[0]
        == "deny"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("thread_id", "thread*"),
        ("agent_id", "agent[1]"),
        ("agent_runtime_name", "agent?"),
        ("selected_skill_slugs", ["skill{a,b}"]),
        ("thread_id", "C:drive"),
        ("thread_id", "thread\x1fhidden"),
    ],
)
def test_permission_builder_rejects_rule_injection(field: str, value: object) -> None:
    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions

    kwargs: dict[str, object] = {
        "thread_id": "thread-a",
        "agent_id": "agent-a",
        "user_id": "user-a",
        "selected_skill_slugs": [],
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match="unsafe path segment"):
        build_filesystem_permissions(**kwargs)  # type: ignore[arg-type]


def test_scoped_offload_rules_do_not_mutate_caller_list() -> None:
    from app.agent_runtime.filesystem_permissions import add_scoped_offload_permissions

    original = []
    effective = add_scoped_offload_permissions(
        original,
        "/.moldy-offload/owner/conversation/actor",
    )

    assert original == []
    assert len(effective) == 2


@pytest.mark.asyncio
async def test_fail_closed_middleware_denies_root_searches_and_safe_errors(tmp_path: Path) -> None:
    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions
    from app.agent_runtime.offload_storage_backends import HiddenFilesystemBackend
    from app.agent_runtime.runtime_component_builder import _build_moldy_filesystem_middleware

    _seed_virtual_data(tmp_path)
    middleware = _build_moldy_filesystem_middleware(
        backend=HiddenFilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        permissions=build_filesystem_permissions(
            thread_id="thread-a",
            agent_id="agent-a",
            user_id="user-a",
            selected_skill_slugs=["selected"],
        ),
    )
    runtime = SimpleNamespace(tool_call_id="call-root")

    results = [
        await tool_coroutine(_tool_by_name(middleware, "ls"))(path="/", runtime=runtime),
        await tool_coroutine(_tool_by_name(middleware, "glob"))(
            pattern="**/*",
            runtime=runtime,
        ),
        await tool_coroutine(_tool_by_name(middleware, "grep"))(
            pattern="secret",
            runtime=runtime,
        ),
        await tool_coroutine(_tool_by_name(middleware, "read_file"))(
            file_path="/conversations/thread-b/output.txt",
            runtime=runtime,
        ),
    ]

    assert all(result.status == "error" for result in results)
    assert {result.content for result in results} == {"Error: filesystem permission denied"}
    assert all(str(tmp_path) not in result.content for result in results)


@pytest.mark.asyncio
async def test_guarded_tool_keeps_runtime_injection_through_tool_call(tmp_path: Path) -> None:
    from langchain.tools import ToolRuntime
    from langgraph.prebuilt import ToolNode

    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions
    from app.agent_runtime.offload_storage_backends import HiddenFilesystemBackend
    from app.agent_runtime.runtime_component_builder import _build_moldy_filesystem_middleware

    own = tmp_path / "conversations" / "thread-a"
    own.mkdir(parents=True)
    (own / "file.txt").write_text("injected")
    middleware = _build_moldy_filesystem_middleware(
        backend=HiddenFilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        permissions=build_filesystem_permissions(
            thread_id="thread-a",
            agent_id="agent-a",
            user_id="user-a",
            selected_skill_slugs=[],
        ),
    )
    tool = _tool_by_name(middleware, "read_file")
    node = ToolNode([tool])
    call: ToolCall = {
        "name": "read_file",
        "args": {"file_path": "/conversations/thread-a/file.txt"},
        "id": "call-injected",
        "type": "tool_call",
    }
    runtime = ToolRuntime(
        state={},
        context=None,
        config={},
        stream_writer=lambda _chunk: None,
        tool_call_id="call-injected",
        store=None,
        tools=[tool],
    )
    injected_call = node._inject_tool_args(call, runtime)  # noqa: SLF001

    result = await tool.ainvoke(injected_call)

    assert result.status == "success"
    assert "injected" in result.content


@pytest.mark.asyncio
async def test_scoped_offload_permissions_follow_actor_backend(tmp_path: Path) -> None:
    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions
    from app.agent_runtime.offload_storage import OffloadIdentity, ScopedOffloadStorage
    from app.agent_runtime.runtime_component_builder import _build_moldy_filesystem_middleware

    identity = OffloadIdentity(owner_id="owner-a", conversation_id="thread-a", run_id="run-a")
    storage = ScopedOffloadStorage(data_dir=tmp_path, identity=identity)
    own = storage.for_actor("00000000-0000-0000-0000-000000000001")
    sibling = storage.for_actor("00000000-0000-0000-0000-000000000002")
    own_history = f"{own.artifacts_root}/conversation_history/session_{'a' * 32}.md"
    own_spill = f"{own.artifacts_root}/large_tool_results/tool-result"
    sibling_spill = f"{sibling.artifacts_root}/large_tool_results/tool-result"
    assert own.write(own_history, "history").error is None
    assert own.write(own_spill, "spill").error is None
    assert sibling.write(sibling_spill, "sibling").error is None

    permissions = build_filesystem_permissions(
        thread_id="thread-a",
        agent_id="agent-a",
        user_id="owner-a",
        selected_skill_slugs=[],
    )
    middleware = _build_moldy_filesystem_middleware(backend=own, permissions=permissions)
    read_file = tool_coroutine(_tool_by_name(middleware, "read_file"))
    runtime = SimpleNamespace(tool_call_id="call-offload")

    history = await read_file(file_path=own_history, runtime=runtime)
    spill = await read_file(file_path=own_spill, runtime=runtime)
    denied = await read_file(file_path=sibling_spill, runtime=runtime)

    assert history.status == "success" and "history" in history.content
    assert spill.status == "success" and "spill" in spill.content
    assert denied.status == "error"
    assert denied.content == "Error: filesystem permission denied"


@pytest.mark.asyncio
async def test_scoped_offload_adapter_denies_sibling_identity_roots(tmp_path: Path) -> None:
    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions
    from app.agent_runtime.offload_storage import OffloadIdentity, ScopedOffloadStorage
    from app.agent_runtime.runtime_component_builder import _build_moldy_filesystem_middleware

    actor = "00000000-0000-0000-0000-000000000001"
    other_actor = "00000000-0000-0000-0000-000000000002"
    own_storage = ScopedOffloadStorage(
        data_dir=tmp_path,
        identity=OffloadIdentity(owner_id="owner-a", conversation_id="thread-a", run_id="run-a"),
    )
    own = own_storage.for_actor(actor)
    sibling_backends = [
        ScopedOffloadStorage(
            data_dir=tmp_path,
            identity=OffloadIdentity(
                owner_id="owner-b",
                conversation_id="thread-a",
                run_id="run-a",
            ),
        ).for_actor(actor),
        ScopedOffloadStorage(
            data_dir=tmp_path,
            identity=OffloadIdentity(
                owner_id="owner-a",
                conversation_id="thread-b",
                run_id="run-a",
            ),
        ).for_actor(actor),
        own_storage.for_actor(other_actor),
        # Same-actor prior-run references intentionally remain readable for resume.
        # A sibling run combined with a different actor must still be isolated.
        ScopedOffloadStorage(
            data_dir=tmp_path,
            identity=OffloadIdentity(
                owner_id="owner-a",
                conversation_id="thread-a",
                run_id="run-b",
            ),
        ).for_actor(other_actor),
    ]
    sibling_paths: list[str] = []
    for index, backend in enumerate(sibling_backends):
        path = f"{backend.artifacts_root}/large_tool_results/sibling-{index}"
        assert backend.write(path, f"SIBLING_SECRET_{index}").error is None
        sibling_paths.append(path)

    middleware = _build_moldy_filesystem_middleware(
        backend=own,
        permissions=build_filesystem_permissions(
            thread_id="thread-a",
            agent_id="agent-a",
            user_id="owner-a",
            selected_skill_slugs=[],
        ),
    )
    read_file = tool_coroutine(_tool_by_name(middleware, "read_file"))
    runtime = SimpleNamespace(tool_call_id="call-identity-matrix")
    results = [await read_file(file_path=path, runtime=runtime) for path in sibling_paths]
    results.append(
        await read_file(
            file_path="/.moldy-internal/offload/forged-run/secret",
            runtime=runtime,
        )
    )

    assert all(result.status == "error" for result in results)
    assert {result.content for result in results} == {"Error: filesystem permission denied"}
    assert all("SIBLING_SECRET" not in result.content for result in results)


@pytest.mark.asyncio
async def test_symlink_swap_after_permission_calculation_is_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent_runtime import runtime_filesystem_secure_backend as secure_backend
    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions
    from app.agent_runtime.offload_storage_backends import HiddenFilesystemBackend
    from app.agent_runtime.runtime_component_builder import _build_moldy_filesystem_middleware

    root = tmp_path / "root"
    own = root / "conversations" / "thread-a"
    sibling = root / "conversations" / "thread-b"
    own.mkdir(parents=True)
    sibling.mkdir(parents=True)
    (own / "safe.txt").write_text("own")
    (sibling / "secret.txt").write_text("sibling-secret")
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("outside-secret")
    original_open = secure_backend._open_existing
    swapped = False

    def swap_after_parent_open(parent: int, name: str):
        nonlocal swapped
        if not swapped:
            swapped = True
            (own / "safe.txt").unlink()
            (own / "safe.txt").symlink_to(outside)
        return original_open(parent, name)

    monkeypatch.setattr(secure_backend, "_open_existing", swap_after_parent_open)
    middleware = _build_moldy_filesystem_middleware(
        backend=HiddenFilesystemBackend(root_dir=root, virtual_mode=True),
        permissions=build_filesystem_permissions(
            thread_id="thread-a",
            agent_id="agent-a",
            user_id="owner-a",
            selected_skill_slugs=[],
        ),
    )

    result = await tool_coroutine(_tool_by_name(middleware, "read_file"))(
        file_path="/conversations/thread-a/safe.txt",
        runtime=SimpleNamespace(tool_call_id="call-swap"),
    )

    assert result.status == "error"
    assert result.content == "Error: filesystem permission denied"
    assert "outside-secret" not in result.content
    assert str(tmp_path) not in result.content


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["write_file", "edit_file"])
async def test_symlink_swap_cannot_mutate_sibling_file(
    tmp_path: Path,
    tool_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent_runtime import runtime_filesystem_secure_backend as secure_backend
    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions
    from app.agent_runtime.offload_storage_backends import HiddenFilesystemBackend
    from app.agent_runtime.runtime_component_builder import _build_moldy_filesystem_middleware

    sibling = tmp_path / "conversations" / "thread-b" / "secret.txt"
    sibling.parent.mkdir(parents=True)
    sibling.write_text("sibling-secret")
    own = tmp_path / "conversations" / "thread-a" / "safe.txt"
    own.parent.mkdir(parents=True)
    own.write_text("own")

    original_open = secure_backend._open_existing
    swapped = False

    def swap_after_parent_open(parent: int, name: str):
        nonlocal swapped
        if not swapped:
            swapped = True
            own.unlink()
            own.symlink_to(sibling)
        return original_open(parent, name)

    monkeypatch.setattr(secure_backend, "_open_existing", swap_after_parent_open)

    middleware = _build_moldy_filesystem_middleware(
        backend=HiddenFilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        permissions=build_filesystem_permissions(
            thread_id="thread-a",
            agent_id="agent-a",
            user_id="owner-a",
            selected_skill_slugs=[],
        ),
    )
    runtime = SimpleNamespace(tool_call_id="call-swap-mutation")
    tool = tool_coroutine(_tool_by_name(middleware, tool_name))

    if tool_name == "write_file":
        result = await tool(
            file_path="/conversations/thread-a/safe.txt",
            content="changed",
            runtime=runtime,
        )
    else:
        result = await tool(
            file_path="/conversations/thread-a/safe.txt",
            old_string="sibling",
            new_string="changed",
            runtime=runtime,
        )

    assert result.status == "error"
    assert result.content == "Error: filesystem permission denied"
    assert sibling.read_text() == "sibling-secret"


def test_scoped_permissions_reject_plain_filesystem_backend(tmp_path: Path) -> None:
    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions
    from app.agent_runtime.runtime_component_builder import _build_moldy_filesystem_middleware

    with pytest.raises(ValueError, match="secure runtime backend"):
        _build_moldy_filesystem_middleware(
            backend=FilesystemBackend(root_dir=tmp_path, virtual_mode=True),
            permissions=build_filesystem_permissions(
                thread_id="thread-a",
                agent_id="agent-a",
                user_id="owner-a",
                selected_skill_slugs=[],
            ),
        )


@pytest.mark.asyncio
async def test_actual_adapter_denies_unknown_upload_and_ambiguous_operations(
    tmp_path: Path,
) -> None:
    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions
    from app.agent_runtime.offload_storage_backends import HiddenFilesystemBackend
    from app.agent_runtime.runtime_component_builder import _build_moldy_filesystem_middleware

    sibling = tmp_path / "uploads" / "sibling.txt"
    sibling.parent.mkdir(parents=True)
    sibling.write_text("sibling")
    collision = tmp_path / "conversations" / "thread-a-collision" / "secret.txt"
    collision.parent.mkdir(parents=True)
    collision.write_text("collision-secret")
    backslash = tmp_path / "conversations" / "thread-a\\secret.txt"
    backslash.write_text("backslash-secret")
    encoded = tmp_path / "conversations" / "thread-a" / "%2fsecret.txt"
    encoded.parent.mkdir(parents=True)
    encoded.write_text("encoded-secret")
    middleware = _build_moldy_filesystem_middleware(
        backend=HiddenFilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        permissions=build_filesystem_permissions(
            thread_id="thread-a",
            agent_id="agent-a",
            user_id="owner-a",
            selected_skill_slugs=[],
        ),
    )
    runtime = SimpleNamespace(tool_call_id="call-deny-matrix")
    read_file = tool_coroutine(_tool_by_name(middleware, "read_file"))
    write_file = tool_coroutine(_tool_by_name(middleware, "write_file"))
    edit_file = tool_coroutine(_tool_by_name(middleware, "edit_file"))

    results = [
        await read_file(file_path="/unknown/secret.txt", runtime=runtime),
        await read_file(file_path="/uploads/sibling.txt", runtime=runtime),
        await read_file(file_path="/conversations/thread-a/../thread-b/secret", runtime=runtime),
        await read_file(
            file_path="/conversations/thread-a-collision/secret.txt",
            runtime=runtime,
        ),
        await read_file(file_path="/conversations/thread-a\\secret.txt", runtime=runtime),
        await read_file(file_path="/conversations/thread-a/%2fsecret.txt", runtime=runtime),
        await read_file(file_path="/conversations/thread-a/%252fsecret.txt", runtime=runtime),
        await read_file(file_path="/conversations/thread-a/%ZZ", runtime=runtime),
        await write_file(file_path="/unknown/secret.txt", content="changed", runtime=runtime),
        await write_file(file_path="/uploads/sibling.txt", content="changed", runtime=runtime),
        await edit_file(
            file_path="/uploads/sibling.txt",
            old_string="sibling",
            new_string="changed",
            runtime=runtime,
        ),
    ]

    assert all(result.status == "error" for result in results)
    assert {result.content for result in results} == {"Error: filesystem permission denied"}
    assert sibling.read_text() == "sibling"
    assert collision.read_text() == "collision-secret"
    assert backslash.read_text() == "backslash-secret"
    assert encoded.read_text() == "encoded-secret"
    assert not (tmp_path / "unknown" / "secret.txt").exists()


@pytest.mark.asyncio
async def test_scoped_policy_denies_arbitrary_execution_without_side_effects(
    tmp_path: Path,
) -> None:
    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions
    from app.agent_runtime.offload_storage_backends import HiddenFilesystemBackend
    from app.agent_runtime.runtime_component_builder import _build_moldy_filesystem_middleware

    marker = tmp_path / "command-ran.txt"
    middleware = _build_moldy_filesystem_middleware(
        backend=HiddenFilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        permissions=build_filesystem_permissions(
            thread_id="thread-a",
            agent_id="agent-a",
            user_id="owner-a",
            selected_skill_slugs=[],
        ),
    )

    result = await tool_coroutine(_tool_by_name(middleware, "execute"))(
        command=f"touch {marker}",
        runtime=SimpleNamespace(tool_call_id="call-execute-denied"),
    )

    assert result.status == "error"
    assert result.content == "Error: filesystem permission denied"
    assert not marker.exists()


@pytest.mark.asyncio
async def test_null_policy_preserves_legacy_tools_but_not_delete_or_execution(
    tmp_path: Path,
) -> None:
    from app.agent_runtime.runtime_component_builder import (
        _MOLDY_FILESYSTEM_TOOL_NAMES,
        _build_moldy_filesystem_middleware,
    )

    own = tmp_path / "file.txt"
    own.write_text("legacy")
    middleware = _build_moldy_filesystem_middleware(
        backend=FilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        permissions=None,
    )
    runtime = SimpleNamespace(tool_call_id="call-legacy")

    read = await tool_coroutine(_tool_by_name(middleware, "read_file"))(
        file_path="/file.txt",
        runtime=runtime,
    )
    execute = await tool_coroutine(_tool_by_name(middleware, "execute"))(
        command="printf should-not-run",
        runtime=runtime,
    )

    assert tuple(tool.name for tool in middleware.tools) == _MOLDY_FILESYSTEM_TOOL_NAMES
    assert "delete" not in {tool.name for tool in middleware.tools}
    assert read.status == "success" and "legacy" in read.content
    assert execute.status == "error"
    assert "should-not-run" not in execute.content


@pytest.mark.asyncio
async def test_empty_policy_denies_all_filesystem_paths(tmp_path: Path) -> None:
    from app.agent_runtime.offload_storage_backends import HiddenFilesystemBackend
    from app.agent_runtime.runtime_component_builder import _build_moldy_filesystem_middleware

    own = tmp_path / "file.txt"
    own.write_text("closed")
    middleware = _build_moldy_filesystem_middleware(
        backend=HiddenFilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        permissions=[],
    )

    result = await tool_coroutine(_tool_by_name(middleware, "read_file"))(
        file_path="/file.txt",
        runtime=SimpleNamespace(tool_call_id="call-empty"),
    )

    assert result.status == "error"
    assert result.content == "Error: filesystem permission denied"


@pytest.mark.asyncio
async def test_interrupt_rule_executes_at_approved_tool_stage(tmp_path: Path) -> None:
    from deepagents.middleware.filesystem import FilesystemPermission

    from app.agent_runtime.offload_storage_backends import HiddenFilesystemBackend
    from app.agent_runtime.runtime_component_builder import _build_moldy_filesystem_middleware

    own = tmp_path / "conversations" / "thread-a" / "approved.txt"
    own.parent.mkdir(parents=True)
    own.write_text("approved")
    middleware = _build_moldy_filesystem_middleware(
        backend=HiddenFilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        permissions=[
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/conversations/thread-a/**"],
                mode="interrupt",
            ),
            FilesystemPermission(operations=["read", "write"], paths=["/**"], mode="deny"),
        ],
    )

    result = await tool_coroutine(_tool_by_name(middleware, "read_file"))(
        file_path="/conversations/thread-a/approved.txt",
        runtime=SimpleNamespace(tool_call_id="call-approved"),
    )

    assert result.status == "success"
    assert "approved" in result.content


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool_name", "pattern_key"), [("glob", "pattern"), ("grep", "glob")])
async def test_search_pattern_rejects_percent_and_traversal(
    tmp_path: Path,
    tool_name: str,
    pattern_key: str,
) -> None:
    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions
    from app.agent_runtime.offload_storage_backends import HiddenFilesystemBackend
    from app.agent_runtime.runtime_component_builder import _build_moldy_filesystem_middleware

    middleware = _build_moldy_filesystem_middleware(
        backend=HiddenFilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        permissions=build_filesystem_permissions(
            thread_id="thread-a",
            agent_id="agent-a",
            user_id="owner-a",
            selected_skill_slugs=[],
        ),
    )
    tool = tool_coroutine(_tool_by_name(middleware, tool_name))
    kwargs = {"path": "/conversations/thread-a", pattern_key: "%ZZ"}
    if tool_name == "grep":
        kwargs["pattern"] = "secret"

    result = await tool(runtime=SimpleNamespace(tool_call_id="call-pattern"), **kwargs)

    assert result.status == "error"
    assert result.content == "Error: filesystem permission denied"


@pytest.mark.parametrize(
    "hidden_root",
    [".moldy-internal", ".moldy-offload", "conversation_history", "large_tool_results"],
)
def test_hidden_backend_file_operations_reject_internal_roots(
    tmp_path: Path,
    hidden_root: str,
) -> None:
    from app.agent_runtime.offload_storage_backends import HiddenFilesystemBackend

    hidden = tmp_path / hidden_root / "secret.txt"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("secret")
    backend = HiddenFilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    virtual = f"/{hidden_root}/secret.txt"

    read = backend.read(virtual)
    write = backend.write(virtual, "changed")
    edit = backend.edit(virtual, "secret", "changed")

    assert read.error == "filesystem access denied"
    assert write.error == "filesystem access denied"
    assert edit.error == "filesystem access denied"
    assert hidden.read_text() == "secret"


@pytest.mark.asyncio
async def test_bulk_tools_skip_symlink_and_hardlink_entries(tmp_path: Path) -> None:
    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions
    from app.agent_runtime.offload_storage_backends import HiddenFilesystemBackend
    from app.agent_runtime.runtime_component_builder import _build_moldy_filesystem_middleware

    own = tmp_path / "conversations" / "thread-a"
    sibling = tmp_path / "conversations" / "thread-b"
    own.mkdir(parents=True)
    sibling.mkdir(parents=True)
    (own / "safe.txt").write_text("ordinary")
    secret = sibling / "secret.txt"
    secret.write_text("TOPSECRET")
    (own / "linked.txt").symlink_to(secret)
    (own / "sibling-dir").symlink_to(sibling, target_is_directory=True)
    (own / "hardlinked.txt").hardlink_to(secret)
    middleware = _build_moldy_filesystem_middleware(
        backend=HiddenFilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        permissions=build_filesystem_permissions(
            thread_id="thread-a",
            agent_id="agent-a",
            user_id="owner-a",
            selected_skill_slugs=[],
        ),
    )
    runtime = SimpleNamespace(tool_call_id="call-bulk-links")

    listing = await tool_coroutine(_tool_by_name(middleware, "ls"))(
        path="/conversations/thread-a",
        runtime=runtime,
    )
    globbed = await tool_coroutine(_tool_by_name(middleware, "glob"))(
        path="/conversations/thread-a",
        pattern="**/*",
        runtime=runtime,
    )
    searched = await tool_coroutine(_tool_by_name(middleware, "grep"))(
        path="/conversations/thread-a",
        pattern="TOPSECRET",
        output_mode="content",
        runtime=runtime,
    )

    assert listing.status == "success"
    assert globbed.status == "success"
    assert searched.status == "success"
    combined = f"{listing.content}\n{globbed.content}\n{searched.content}"
    assert "linked.txt" not in combined
    assert "sibling-dir" not in combined
    assert "hardlinked.txt" not in combined
    assert "TOPSECRET" not in combined


@pytest.mark.asyncio
async def test_secure_grep_supports_allowed_single_file_path(tmp_path: Path) -> None:
    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions
    from app.agent_runtime.offload_storage_backends import HiddenFilesystemBackend
    from app.agent_runtime.runtime_component_builder import _build_moldy_filesystem_middleware

    own = tmp_path / "conversations" / "thread-a" / "single.txt"
    own.parent.mkdir(parents=True)
    own.write_text("needle\n")
    middleware = _build_moldy_filesystem_middleware(
        backend=HiddenFilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        permissions=build_filesystem_permissions(
            thread_id="thread-a",
            agent_id="agent-a",
            user_id="owner-a",
            selected_skill_slugs=[],
        ),
    )

    result = await tool_coroutine(_tool_by_name(middleware, "grep"))(
        path="/conversations/thread-a/single.txt",
        pattern="needle",
        output_mode="content",
        runtime=SimpleNamespace(tool_call_id="call-single-grep"),
    )

    assert result.status == "success"
    assert "needle" in result.content


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["glob", "grep"])
async def test_bulk_search_rejects_directory_rename_swap_without_leaking_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
) -> None:
    from app.agent_runtime import runtime_filesystem_secure_search as secure_search
    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions
    from app.agent_runtime.offload_storage_backends import HiddenFilesystemBackend
    from app.agent_runtime.runtime_component_builder import _build_moldy_filesystem_middleware

    own = tmp_path / "conversations" / "thread-a"
    sibling = tmp_path / "conversations" / "thread-b"
    target = own / "target"
    replacement = sibling / "sibling-target"
    target.mkdir(parents=True)
    replacement.mkdir(parents=True)
    (target / "ordinary.txt").write_text("ordinary")
    (replacement / "sibling-secret.txt").write_text("SIBLING_RENAME_SECRET")
    original_open = secure_search.os.open
    swapped = False

    def swap_between_lstat_and_open(
        path: str | bytes,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if not swapped and path == "target" and dir_fd is not None:
            swapped = True
            target.rename(own / "original-target")
            replacement.rename(target)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(secure_search.os, "open", swap_between_lstat_and_open)
    middleware = _build_moldy_filesystem_middleware(
        backend=HiddenFilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        permissions=build_filesystem_permissions(
            thread_id="thread-a",
            agent_id="agent-a",
            user_id="owner-a",
            selected_skill_slugs=[],
        ),
    )
    tool = tool_coroutine(_tool_by_name(middleware, tool_name))
    kwargs: dict[str, object] = {
        "path": "/conversations/thread-a",
        "runtime": SimpleNamespace(tool_call_id=f"call-{tool_name}-rename-swap"),
    }
    if tool_name == "glob":
        kwargs["pattern"] = "**/*"
    else:
        kwargs.update(pattern="SIBLING_RENAME_SECRET", output_mode="content")

    result = await tool(**kwargs)

    assert swapped
    assert result.status == "error"
    assert result.content == "Error: filesystem permission denied"
    assert "sibling-secret.txt" not in result.content
    assert "SIBLING_RENAME_SECRET" not in result.content
    assert str(tmp_path) not in result.content


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["glob", "grep"])
async def test_bulk_search_rejects_file_rename_swap_without_leaking_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
) -> None:
    from app.agent_runtime import runtime_filesystem_secure_search as secure_search
    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions
    from app.agent_runtime.offload_storage_backends import HiddenFilesystemBackend
    from app.agent_runtime.runtime_component_builder import _build_moldy_filesystem_middleware

    own = tmp_path / "conversations" / "thread-a"
    sibling = tmp_path / "conversations" / "thread-b"
    victim = own / "victim.txt"
    replacement = sibling / "sibling-secret.txt"
    own.mkdir(parents=True)
    sibling.mkdir(parents=True)
    victim.write_text("ordinary")
    replacement.write_text("SIBLING_FILE_RENAME_SECRET")
    original_open = secure_search.os.open
    swapped = False

    def swap_between_lstat_and_open(
        path: str | bytes,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if not swapped and path == "victim.txt" and dir_fd is not None:
            swapped = True
            victim.rename(own / "original-victim.txt")
            replacement.rename(victim)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(secure_search.os, "open", swap_between_lstat_and_open)
    middleware = _build_moldy_filesystem_middleware(
        backend=HiddenFilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        permissions=build_filesystem_permissions(
            thread_id="thread-a",
            agent_id="agent-a",
            user_id="owner-a",
            selected_skill_slugs=[],
        ),
    )
    tool = tool_coroutine(_tool_by_name(middleware, tool_name))
    kwargs: dict[str, object] = {
        "path": "/conversations/thread-a",
        "runtime": SimpleNamespace(tool_call_id=f"call-{tool_name}-file-rename-swap"),
    }
    if tool_name == "glob":
        kwargs["pattern"] = "**/*"
    else:
        kwargs.update(pattern="SIBLING_FILE_RENAME_SECRET", output_mode="content")

    result = await tool(**kwargs)

    assert swapped
    assert result.status == "error"
    assert result.content == "Error: filesystem permission denied"
    assert "sibling-secret.txt" not in result.content
    assert "SIBLING_FILE_RENAME_SECRET" not in result.content
    assert str(tmp_path) not in result.content


@pytest.mark.parametrize("operation", ["download", "upload"])
def test_bulk_transfer_rejects_parent_rename_swap_without_sibling_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    from app.agent_runtime import runtime_filesystem_secure_backend as secure_backend
    from app.agent_runtime.offload_storage_backends import HiddenFilesystemBackend

    own = tmp_path / "conversations" / "thread-a"
    sibling = tmp_path / "conversations" / "thread-b"
    own.mkdir(parents=True)
    sibling.mkdir(parents=True)
    own_file = own / "payload.bin"
    sibling_file = sibling / "payload.bin"
    own_file.write_bytes(b"own")
    sibling_file.write_bytes(b"SIBLING_BULK_SECRET")
    original_open = secure_backend.os.open
    swapped = False

    def swap_parent_before_open(
        path: str | bytes,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        absolute_target = str(own_file)
        if not swapped and (path == "thread-a" or str(path) == absolute_target):
            swapped = True
            own.rename(tmp_path / "parked-thread-a")
            sibling.rename(own)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(secure_backend.os, "open", swap_parent_before_open)
    backend = HiddenFilesystemBackend(root_dir=tmp_path, virtual_mode=True)

    if operation == "download":
        response = backend.download_files(["/conversations/thread-a/payload.bin"])[0]
        assert response.content is None
    else:
        response = backend.upload_files(
            [("/conversations/thread-a/payload.bin", b"attacker-write")]
        )[0]

    assert swapped
    assert response.error == "permission_denied"
    assert (own / "payload.bin").read_bytes() == b"SIBLING_BULK_SECRET"
    assert (tmp_path / "parked-thread-a" / "payload.bin").read_bytes() == b"own"


def test_bulk_download_rejects_file_rename_swap_without_sibling_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent_runtime import runtime_filesystem_secure_backend as secure_backend
    from app.agent_runtime.offload_storage_backends import HiddenFilesystemBackend

    own = tmp_path / "conversations" / "thread-a"
    sibling = tmp_path / "conversations" / "thread-b"
    own.mkdir(parents=True)
    sibling.mkdir(parents=True)
    victim = own / "payload.bin"
    replacement = sibling / "sibling-payload.bin"
    victim.write_bytes(b"own")
    replacement.write_bytes(b"SIBLING_FILE_SECRET")
    original_open = secure_backend.os.open
    swapped = False

    def swap_file_before_open(
        path: str | bytes,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if not swapped and path == "payload.bin" and dir_fd is not None:
            swapped = True
            victim.rename(own / "original-payload.bin")
            replacement.rename(victim)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(secure_backend.os, "open", swap_file_before_open)
    backend = HiddenFilesystemBackend(root_dir=tmp_path, virtual_mode=True)

    response = backend.download_files(["/conversations/thread-a/payload.bin"])[0]

    assert swapped
    assert response.error == "permission_denied"
    assert response.content is None
    assert (own / "payload.bin").read_bytes() == b"SIBLING_FILE_SECRET"
    assert (own / "original-payload.bin").read_bytes() == b"own"


@pytest.mark.asyncio
async def test_missing_agent_memory_file_does_not_abort_memory_startup(tmp_path: Path) -> None:
    """Missing optional AGENTS.md must not abort Deep Agents memory startup."""

    from deepagents.middleware.memory import MemoryMiddleware

    from app.agent_runtime.offload_storage_backends import HiddenFilesystemBackend

    (tmp_path / "agents" / "agent-a").mkdir(parents=True)
    backend = HiddenFilesystemBackend(root_dir=tmp_path, virtual_mode=True)

    response = backend.download_files(["/agents/agent-a/AGENTS.md"])[0]

    assert response.content is None
    assert response.error == "file_not_found"

    update = await MemoryMiddleware(
        backend=backend,
        sources=["/agents/agent-a/AGENTS.md"],
    ).abefore_agent({"messages": []}, MagicMock(), {})

    assert update == {"memory_contents": {}}


@pytest.mark.parametrize(
    "hidden_root",
    [".moldy-internal", ".moldy-offload", "conversation_history", "large_tool_results"],
)
def test_bulk_transfer_rejects_hidden_roots(tmp_path: Path, hidden_root: str) -> None:
    from app.agent_runtime.offload_storage_backends import HiddenFilesystemBackend

    hidden = tmp_path / hidden_root / "secret.bin"
    hidden.parent.mkdir(parents=True)
    hidden.write_bytes(b"hidden-secret")
    backend = HiddenFilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    virtual = f"/{hidden_root}/secret.bin"

    downloaded = backend.download_files([virtual])[0]
    uploaded = backend.upload_files([(virtual, b"changed")])[0]

    assert downloaded.content is None
    assert downloaded.error == "permission_denied"
    assert uploaded.error == "permission_denied"
    assert hidden.read_bytes() == b"hidden-secret"


@pytest.mark.parametrize("file_path", ["/single.txt", "/nested"])
@pytest.mark.parametrize(
    ("max_count", "expected_count", "expected_truncated"),
    [(0, 0, True), (1, 1, True), (2, 2, False)],
)
def test_secure_grep_matches_upstream_max_count_contract(
    tmp_path: Path,
    file_path: str,
    max_count: int,
    expected_count: int,
    expected_truncated: bool,
) -> None:
    from app.agent_runtime.offload_storage_backends import HiddenFilesystemBackend

    single = tmp_path / "single.txt"
    nested = tmp_path / "nested" / "matches.txt"
    single.write_text("needle one\nneedle two\n")
    nested.parent.mkdir()
    nested.write_text("needle one\nneedle two\n")
    backend = HiddenFilesystemBackend(root_dir=tmp_path, virtual_mode=True)

    result = backend.grep("needle", file_path, max_count=max_count)

    assert result.error is None
    assert len(result.matches or []) == expected_count
    assert result.truncated is expected_truncated


@pytest.mark.asyncio
async def test_scoped_policy_constructs_with_executable_backend_but_denies_execute(
    tmp_path: Path,
) -> None:
    from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions
    from app.agent_runtime.runtime_component_builder import _build_moldy_filesystem_middleware
    from app.agent_runtime.runtime_filesystem_secure_backend import SecureRuntimeFilesystemBackend

    marker = tmp_path / "execute-was-called"

    class ExecutableSecureBackend(SecureRuntimeFilesystemBackend, SandboxBackendProtocol):
        @property
        def id(self) -> str:
            return "executable-secure-test"

        def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
            del command, timeout
            marker.write_text("called")
            return ExecuteResponse(output="executed", exit_code=0)

    middleware = _build_moldy_filesystem_middleware(
        backend=ExecutableSecureBackend(root_dir=tmp_path, virtual_mode=True),
        permissions=build_filesystem_permissions(
            thread_id="thread-a",
            agent_id="agent-a",
            user_id="owner-a",
            selected_skill_slugs=[],
        ),
    )

    result = await tool_coroutine(_tool_by_name(middleware, "execute"))(
        command="ignored",
        runtime=SimpleNamespace(tool_call_id="call-executable-denied"),
    )
    sibling = tmp_path / "conversations" / "thread-b" / "secret.txt"
    sibling.parent.mkdir(parents=True)
    sibling.write_text("sibling-secret")
    read = await tool_coroutine(_tool_by_name(middleware, "read_file"))(
        file_path="/conversations/thread-b/secret.txt",
        runtime=SimpleNamespace(tool_call_id="call-executable-read-denied"),
    )

    assert result.status == "error"
    assert result.content == "Error: filesystem permission denied"
    assert read.status == "error"
    assert read.content == "Error: filesystem permission denied"
    assert not marker.exists()


@pytest.mark.asyncio
async def test_null_policy_preserves_executable_backend(tmp_path: Path) -> None:
    from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

    from app.agent_runtime.runtime_component_builder import _build_moldy_filesystem_middleware
    from app.agent_runtime.runtime_filesystem_secure_backend import SecureRuntimeFilesystemBackend

    marker = tmp_path / "legacy-execute-was-called"

    class LegacyExecutableBackend(SecureRuntimeFilesystemBackend, SandboxBackendProtocol):
        @property
        def id(self) -> str:
            return "legacy-executable-test"

        def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
            del command, timeout
            marker.write_text("called")
            return ExecuteResponse(output="legacy-executed", exit_code=0)

    middleware = _build_moldy_filesystem_middleware(
        backend=LegacyExecutableBackend(root_dir=tmp_path, virtual_mode=True),
        permissions=None,
    )

    result = await tool_coroutine(_tool_by_name(middleware, "execute"))(
        command="ignored",
        runtime=SimpleNamespace(tool_call_id="call-legacy-execute"),
    )

    assert result.status == "success"
    assert "legacy-executed" in result.content
    assert marker.read_text() == "called"


@pytest.mark.asyncio
async def test_scoped_middleware_copies_caller_permissions(tmp_path: Path) -> None:
    from app.agent_runtime.filesystem_permissions import build_filesystem_permissions
    from app.agent_runtime.offload_storage_backends import HiddenFilesystemBackend
    from app.agent_runtime.runtime_component_builder import _build_moldy_filesystem_middleware

    own = tmp_path / "conversations" / "thread-a" / "owned.txt"
    own.parent.mkdir(parents=True)
    own.write_text("owned")
    permissions = build_filesystem_permissions(
        thread_id="thread-a",
        agent_id="agent-a",
        user_id="owner-a",
        selected_skill_slugs=[],
    )
    middleware = _build_moldy_filesystem_middleware(
        backend=HiddenFilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        permissions=permissions,
    )

    permissions.clear()
    result = await tool_coroutine(_tool_by_name(middleware, "read_file"))(
        file_path="/conversations/thread-a/owned.txt",
        runtime=SimpleNamespace(tool_call_id="call-copied-permissions"),
    )

    assert result.status == "success"
    assert "owned" in result.content
