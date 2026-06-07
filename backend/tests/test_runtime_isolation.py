"""M5 Slice E — Runtime isolation (Phase 1 출시 게이트).

Spec §9 + deletion-analysis §1.(a). Stage 2 surface (젠슨 2026-05-19):

* ``SkillToolContext`` / ``SkillRuntimeDescriptor`` in
  ``app.marketplace.skill_runtime``.
* ``build_skill_runtime_context(cfg, *, data_dir)`` materializes each
  attached skill into ``data_dir / "runtime" / <thread_id> / "skills" / <slug>``
  via ``copytree(symlinks=False)``.
* ``_create_skill_execute_tool(ctx)`` closes over the context and
  rejects unknown slugs + paths outside ``ctx.runtime_root``.
* ``cleanup_stale_runtime_roots(data_dir, retention_seconds=...)`` sweeps
  on-disk roots older than the retention window.

Tests pin the four security gates: per-thread isolation, selected-skill
mount, traversal rejection, retention cleanup.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest

from app.agent_runtime import skill_executor
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.skill_executor import _create_skill_execute_tool
from app.marketplace.skill_runtime import (
    build_skill_runtime_context,
    cleanup_stale_runtime_roots,
)
from tests.tool_helpers import tool_coroutine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cfg(
    *,
    thread_id: str,
    skills: list[dict] | None = None,
    user_id: str | None = None,
    agent_id: str | None = None,
    agent_runtime_name: str | None = None,
) -> AgentConfig:
    """Construct a minimal ``AgentConfig`` for runtime tests.

    Skips the production hook context (``agent_id`` left None unless the
    caller wants the ``__post_init__`` guard exercised)."""

    return AgentConfig(
        provider="anthropic",
        model_name="claude-sonnet-4-5",
        api_key=None,
        base_url=None,
        system_prompt="",
        tools_config=[],
        thread_id=thread_id,
        agent_skills=skills,
        user_id=user_id,
        agent_id=agent_id,
        agent_runtime_name=agent_runtime_name,
    )


def _seed_skill_on_disk(root: Path, *, slug: str, body: str = "# canonical\n") -> Path:
    """Create an on-disk package skill source. Returns its directory."""

    src = root / "_canonical_skills" / slug
    src.mkdir(parents=True, exist_ok=True)
    (src / "SKILL.md").write_text(body)
    (src / "scripts").mkdir(exist_ok=True)
    (src / "scripts" / "run.py").write_text(
        "import os, sys\nprint('skill=' + os.path.basename(os.getcwd()))\nsys.exit(0)\n"
    )
    return src


def _skill_descriptor_dict(skill_id: uuid.UUID, slug: str, src: Path) -> dict:
    return {
        "id": str(skill_id),
        "slug": slug,
        "name": slug,
        "kind": "package",
        "storage_path": str(src),
        "description": "",
    }


# ===========================================================================
# Per-thread runtime root
# ===========================================================================


class TestPerThreadRuntimeRoot:
    def test_runtime_context_uses_configured_output_root(self, tmp_path: Path) -> None:
        """Skill subprocess outputs follow the configured conversation output root.

        Artifact recording scans ``settings.conversation_output_dir``. The skill
        runtime must write generated files under that same root or file events
        and artifact manifests can miss successful outputs when the setting is
        customized.
        """

        cfg = _make_cfg(thread_id="thread-custom-output", skills=[])
        output_root = tmp_path / "custom-conversations"

        ctx = build_skill_runtime_context(cfg, data_dir=tmp_path, output_root=output_root)

        assert ctx.output_dir == (output_root / "thread-custom-output").resolve()

    @pytest.mark.asyncio
    async def test_per_thread_runtime_root_isolated(self, tmp_path: Path) -> None:
        """Two distinct ``thread_id`` values materialize to two distinct
        runtime root directories. Content written into thread A's root
        is invisible from thread B's root.
        """

        src = _seed_skill_on_disk(tmp_path, slug="s1")
        skill_id = uuid.uuid4()

        cfg_a = _make_cfg(
            thread_id="thread-a",
            skills=[_skill_descriptor_dict(skill_id, "s1", src)],
        )
        cfg_b = _make_cfg(
            thread_id="thread-b",
            skills=[_skill_descriptor_dict(skill_id, "s1", src)],
        )
        ctx_a = build_skill_runtime_context(cfg_a, data_dir=tmp_path)
        ctx_b = build_skill_runtime_context(cfg_b, data_dir=tmp_path)

        # Two separate runtime roots.
        assert ctx_a.runtime_root != ctx_b.runtime_root
        assert ctx_a.runtime_root.exists() and ctx_b.runtime_root.exists()
        assert "thread-a" in str(ctx_a.runtime_root)
        assert "thread-b" in str(ctx_b.runtime_root)

        # Both materialized their own copy of the skill (separate inodes).
        skill_a = ctx_a.descriptors["s1"].runtime_storage_path / "SKILL.md"
        skill_b = ctx_b.descriptors["s1"].runtime_storage_path / "SKILL.md"
        assert skill_a.exists() and skill_b.exists()
        assert skill_a != skill_b
        # symlinks=False ⇒ writes through one path do NOT mutate the other.
        skill_a.write_text("# THREAD A ONLY\n")
        assert skill_b.read_text() == "# canonical\n", (
            "copytree(symlinks=False) failed — thread-a write leaked into thread-b's runtime root"
        )
        # And neither leaked back to the canonical source.
        assert (src / "SKILL.md").read_text() == "# canonical\n", (
            "copy materialization wrote through to canonical storage — "
            "user-owned skill source corrupted"
        )

    @pytest.mark.asyncio
    async def test_unselected_skill_unreachable(self, tmp_path: Path) -> None:
        """User has two on-disk skills (s1, s2) but the agent attaches
        only s1. ``execute_in_skill('/runtime/<thread>/skills/s2/', ...)``
        returns the documented "not attached" error.

        This is the regression guard for the legacy broad ``/skills/``
        mount that leaked sibling skills (deletion-analysis §1.(a))."""

        src_s1 = _seed_skill_on_disk(tmp_path, slug="s1")
        _seed_skill_on_disk(tmp_path, slug="s2")  # exists on disk, not attached

        cfg = _make_cfg(
            thread_id="thread-only-s1",
            skills=[_skill_descriptor_dict(uuid.uuid4(), "s1", src_s1)],
        )
        ctx = build_skill_runtime_context(cfg, data_dir=tmp_path)
        tool = _create_skill_execute_tool(ctx)
        execute = tool_coroutine(tool)

        # The descriptor map only contains s1 — s2 is filtered out at
        # build time, so the runtime tool rejects it.
        result = await execute(
            skill_directory="/runtime/thread-only-s1/skills/s2/",
            command="python scripts/run.py",
        )
        assert "not attached" in result, f"Expected unselected-slug rejection, got: {result!r}"
        assert "s2" in result

    @pytest.mark.asyncio
    async def test_cross_user_skill_unreachable(self, tmp_path: Path) -> None:
        """User A's skill source on disk; User B's thread attaches none.

        User B's tool must NOT be able to read User A's skill — the
        descriptor map is empty so every slug is rejected.
        """

        src_a = _seed_skill_on_disk(tmp_path, slug="user-a-skill")

        cfg_a = _make_cfg(
            thread_id="thread-user-a",
            user_id=str(uuid.uuid4()),
            skills=[_skill_descriptor_dict(uuid.uuid4(), "user-a-skill", src_a)],
        )
        cfg_b = _make_cfg(
            thread_id="thread-user-b",
            user_id=str(uuid.uuid4()),
            skills=None,  # User B has no attached skills.
        )
        build_skill_runtime_context(cfg_a, data_dir=tmp_path)
        ctx_b = build_skill_runtime_context(cfg_b, data_dir=tmp_path)

        # User B has empty descriptors → every slug rejected.
        assert ctx_b.descriptors == {}

        tool_b = _create_skill_execute_tool(ctx_b)
        result = await tool_coroutine(tool_b)(
            skill_directory="/runtime/thread-user-b/skills/user-a-skill/",
            command="python scripts/run.py",
        )
        assert "not attached" in result

        # And User B's runtime root is a separate dir from User A's.
        # The thread_id scope is the per-user partition.
        assert "thread-user-a" not in str(ctx_b.runtime_root)

    def test_runtime_root_can_be_scoped_by_agent_runtime_name(
        self,
        tmp_path: Path,
    ) -> None:
        src = _seed_skill_on_disk(tmp_path, slug="calendar")
        cfg = _make_cfg(
            thread_id="thread-1",
            agent_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            agent_runtime_name="agent_1234abcd",
            skills=[
                _skill_descriptor_dict(
                    uuid.uuid4(),
                    "calendar",
                    src,
                )
            ],
        )

        ctx = build_skill_runtime_context(cfg, data_dir=tmp_path)

        assert (
            ctx.runtime_root
            == tmp_path / "runtime" / "thread-1" / "agents" / "agent_1234abcd" / "skills"
        )
        assert ctx.descriptors["calendar"].runtime_storage_path == ctx.runtime_root / "calendar"


# ===========================================================================
# execute_in_skill path validation
# ===========================================================================


class TestExecuteInSkillPathValidation:
    @pytest.mark.asyncio
    async def test_execute_in_skill_uses_skill_timeout_from_execution_profile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        observed_timeouts: list[float | None] = []
        real_wait_for = skill_executor.asyncio.wait_for

        async def _recording_wait_for(awaitable, **kwargs):
            timeout = kwargs.get("timeout")
            observed_timeouts.append(timeout)
            return await real_wait_for(awaitable, timeout=timeout)

        monkeypatch.setattr(skill_executor.asyncio, "wait_for", _recording_wait_for)

        src = _seed_skill_on_disk(tmp_path, slug="slow-image")
        (src / "scripts" / "probe.py").write_text("print('ok')\n")
        descriptor = _skill_descriptor_dict(uuid.uuid4(), "slow-image", src)
        descriptor["execution_profile"] = {"timeout_seconds": 420}
        cfg = _make_cfg(
            thread_id="thread-timeout",
            skills=[descriptor],
        )
        ctx = build_skill_runtime_context(cfg, data_dir=tmp_path)
        tool = _create_skill_execute_tool(ctx)
        execute = tool_coroutine(tool)

        result = await execute(
            skill_directory="/runtime/thread-timeout/skills/slow-image/",
            command="python scripts/probe.py",
        )

        assert "ok" in result
        assert observed_timeouts == [420]

    @pytest.mark.asyncio
    async def test_execute_in_skill_preserves_tls_ca_bundle_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Skill subprocesses need the curated TLS CA bundle from the parent
        process, while unrelated host env vars must remain isolated."""

        ssl_cert = tmp_path / "combined.pem"
        ssl_cert.write_text("test-ca\n")
        requests_bundle = tmp_path / "requests.pem"
        requests_bundle.write_text("test-requests-ca\n")
        monkeypatch.setenv("SSL_CERT_FILE", str(ssl_cert))
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(requests_bundle))
        monkeypatch.setenv("SECRET_PASTE", "host-leak-value")

        src = _seed_skill_on_disk(tmp_path, slug="tls")
        (src / "scripts" / "env_probe.py").write_text(
            "import os\n"
            "print('SSL_CERT_FILE=' + os.environ.get('SSL_CERT_FILE', ''))\n"
            "print('REQUESTS_CA_BUNDLE=' + os.environ.get('REQUESTS_CA_BUNDLE', ''))\n"
            "print('SECRET_PASTE=' + os.environ.get('SECRET_PASTE', ''))\n"
        )
        cfg = _make_cfg(
            thread_id="thread-tls",
            skills=[_skill_descriptor_dict(uuid.uuid4(), "tls", src)],
        )
        ctx = build_skill_runtime_context(cfg, data_dir=tmp_path)
        tool = _create_skill_execute_tool(ctx)
        execute = tool_coroutine(tool)

        result = await execute(
            skill_directory="/runtime/thread-tls/skills/tls/",
            command="python scripts/env_probe.py",
        )

        assert f"SSL_CERT_FILE={ssl_cert}" in result
        assert f"REQUESTS_CA_BUNDLE={requests_bundle}" in result
        assert "SECRET_PASTE=host-leak-value" not in result

    @pytest.mark.asyncio
    async def test_execute_in_skill_allows_curl_with_base_assignment(self, tmp_path: Path) -> None:
        """k-skill docs often show a BASE=... + curl snippet. The runtime
        should execute that shape without opening arbitrary shell execution."""

        src = _seed_skill_on_disk(tmp_path, slug="curl")
        (src / "payload.txt").write_text("curl-ok\n")
        cfg = _make_cfg(
            thread_id="thread-curl",
            skills=[_skill_descriptor_dict(uuid.uuid4(), "curl", src)],
        )
        ctx = build_skill_runtime_context(cfg, data_dir=tmp_path)
        payload_url = (ctx.descriptors["curl"].runtime_storage_path / "payload.txt").as_uri()
        tool = _create_skill_execute_tool(ctx)
        execute = tool_coroutine(tool)

        result = await execute(
            skill_directory="/runtime/thread-curl/skills/curl/",
            command=(f'BASE="${{KSKILL_PROXY_BASE_URL:-{payload_url}}}"\ncurl -fsS "${{BASE}}"'),
        )

        assert "curl-ok" in result

    @pytest.mark.asyncio
    async def test_execute_in_skill_rejects_outside_runtime_root(self, tmp_path: Path) -> None:
        """Absolute paths (``/etc/passwd``) and traversal (``../`` runs)
        must NOT escape the runtime root.

        Implementation detail: the tool uses ``Path(...).name`` to
        extract the slug from the request, so the path prefix is
        decorative. The real security gate is the descriptor lookup —
        anything whose final segment isn't an attached slug is rejected.
        """

        src = _seed_skill_on_disk(tmp_path, slug="s1")
        cfg = _make_cfg(
            thread_id="thread-traversal",
            skills=[_skill_descriptor_dict(uuid.uuid4(), "s1", src)],
        )
        ctx = build_skill_runtime_context(cfg, data_dir=tmp_path)
        tool = _create_skill_execute_tool(ctx)
        execute = tool_coroutine(tool)

        # Each payload's final segment is NOT in the descriptor map →
        # "skill not attached" rejection without running anything.
        for attempt in (
            "/etc/passwd",
            "/etc/",
            "../../../etc/passwd",
            "/var/log/messages",
            # Trailing slash collapses to empty Path.name — also rejected
            # because the empty slug is not a key.
            "/runtime/thread-traversal/skills/",
        ):
            result = await execute(
                skill_directory=attempt,
                command="python scripts/run.py",
            )
            assert "Error:" in result, (
                f"Traversal payload {attempt!r} did not return an error: {result!r}"
            )
            assert not result.startswith("skill="), (
                f"Script ran for traversal payload {attempt!r}: {result!r}"
            )

    @pytest.mark.asyncio
    async def test_cross_thread_path_prefix_does_not_escape_local_root(
        self, tmp_path: Path
    ) -> None:
        """If the LLM passes ``/runtime/<other-thread>/skills/s1/`` from
        thread A's context, the slug ``s1`` IS in the local descriptor
        map — so the script runs, but **strictly in thread A's runtime
        root**. The decorative ``other-thread`` prefix has zero effect.

        This pins the contract that the descriptor map (not the path
        prefix) is the security boundary. Future refactors that try to
        "validate" the prefix would break the existing UX without
        improving the gate.
        """

        src = _seed_skill_on_disk(tmp_path, slug="s1")
        cfg_a = _make_cfg(
            thread_id="thread-a",
            skills=[_skill_descriptor_dict(uuid.uuid4(), "s1", src)],
        )
        cfg_b = _make_cfg(
            thread_id="thread-b",
            skills=[_skill_descriptor_dict(uuid.uuid4(), "s1", src)],
        )
        ctx_a = build_skill_runtime_context(cfg_a, data_dir=tmp_path)
        build_skill_runtime_context(cfg_b, data_dir=tmp_path)
        # Mark thread B's copy so we can detect leak.
        (
            (tmp_path / "runtime" / "thread-b" / "skills" / "s1" / "SKILL.md").write_text(
                "# THREAD B PRIVATE MARKER\n"
            )
        )

        tool_a = _create_skill_execute_tool(ctx_a)
        result = await tool_coroutine(tool_a)(
            # Spoofed prefix points at thread B but slug is still s1.
            skill_directory="/runtime/thread-b/skills/s1/",
            command="python -c 'import pathlib; print(pathlib.Path(\"SKILL.md\").read_text())'",
        )
        assert "THREAD B PRIVATE MARKER" not in result, (
            "Prefix spoof reached thread B's runtime root — isolation broken"
        )
        # Thread A's canonical body is what came back.
        assert "canonical" in result, f"Expected thread A's SKILL.md content in result: {result!r}"

    @pytest.mark.asyncio
    async def test_execute_in_skill_rejects_unselected_slug(self, tmp_path: Path) -> None:
        """Slug not in ``ctx.descriptors`` → "skill not attached" error.

        Same as the unreachable-skill test but exercised through the
        execute_in_skill tool surface end-to-end, with an explicit
        message-content check."""

        src = _seed_skill_on_disk(tmp_path, slug="attached")
        cfg = _make_cfg(
            thread_id="thread-x",
            skills=[_skill_descriptor_dict(uuid.uuid4(), "attached", src)],
        )
        ctx = build_skill_runtime_context(cfg, data_dir=tmp_path)
        tool = _create_skill_execute_tool(ctx)
        execute = tool_coroutine(tool)

        result = await execute(
            skill_directory="/runtime/thread-x/skills/some-other-slug/",
            command="python scripts/run.py",
        )
        assert result == ("Error: skill not attached to this agent: some-other-slug"), (
            f"unexpected error message: {result!r}"
        )


# ===========================================================================
# Stale root retention
# ===========================================================================


class TestRuntimeRootCleanup:
    @pytest.mark.asyncio
    async def test_runtime_root_cleanup_on_stale(self, tmp_path: Path) -> None:
        """``cleanup_stale_runtime_roots(retention_seconds=...)`` deletes
        directories whose mtime is older than the threshold. Fresh
        directories survive.

        Strategy: create two ``data/runtime/<thread>`` entries, age one
        by editing its mtime to two hours ago, sweep, assert exactly
        the old one is gone.
        """

        src = _seed_skill_on_disk(tmp_path, slug="s1")
        cfg_fresh = _make_cfg(
            thread_id="fresh-thread",
            skills=[_skill_descriptor_dict(uuid.uuid4(), "s1", src)],
        )
        cfg_stale = _make_cfg(
            thread_id="stale-thread",
            skills=[_skill_descriptor_dict(uuid.uuid4(), "s1", src)],
        )
        build_skill_runtime_context(cfg_fresh, data_dir=tmp_path)
        build_skill_runtime_context(cfg_stale, data_dir=tmp_path)

        runtime_parent = tmp_path / "runtime"
        fresh_dir = runtime_parent / "fresh-thread"
        stale_dir = runtime_parent / "stale-thread"
        assert fresh_dir.exists() and stale_dir.exists()

        # Age the stale dir by two hours.
        two_hours_ago = time.time() - 2 * 3600
        os.utime(stale_dir, (two_hours_ago, two_hours_ago))

        removed = cleanup_stale_runtime_roots(tmp_path, retention_seconds=3600)

        assert removed == 1, f"expected 1 removal, got {removed}"
        assert not stale_dir.exists(), "stale runtime root not cleaned"
        assert fresh_dir.exists(), "fresh runtime root incorrectly swept"

    def test_runtime_root_cleanup_no_runtime_dir_is_noop(self, tmp_path: Path) -> None:
        """Empty ``data/`` (no ``runtime/`` subdir) must not raise — the
        sweep is best-effort and idempotent on startup before any
        conversation exists."""

        # Fresh tmp_path has no `runtime/` dir.
        removed = cleanup_stale_runtime_roots(tmp_path, retention_seconds=60)
        assert removed == 0


# ===========================================================================
# Smoke — import surface
# ===========================================================================


class TestImportSurfaceUnchanged:
    def test_executor_exposes_data_dir_and_skill_execute_tool(self) -> None:
        from app.agent_runtime import executor

        assert hasattr(executor, "_DATA_DIR")
        assert hasattr(executor, "_create_skill_execute_tool")
        assert callable(executor.build_agent)

    def test_executor_facade_exports_runtime_entrypoints(self) -> None:
        from app.agent_runtime import executor

        for name in (
            "AgentConfig",
            "RuntimeComponents",
            "_DATA_DIR",
            "build_agent",
            "_create_skill_execute_tool",
            "_build_mcp_tools",
            "_prepare_runtime_components",
            "_prepare_agent",
            "execute_agent_stream",
            "resume_agent_stream",
            "execute_agent_invoke",
        ):
            assert hasattr(executor, name), f"executor facade missing {name!r}"

    def test_skill_runtime_module_public_surface(self) -> None:
        """Stage 2 added 4 symbols to ``app.marketplace.skill_runtime``.
        Tests reach into the dataclasses + builder + cleanup directly —
        guard against accidental rename or relocation."""

        from app.marketplace import skill_runtime as sr

        for name in (
            "SkillRuntimeDescriptor",
            "SkillToolContext",
            "build_skill_runtime_context",
            "cleanup_stale_runtime_roots",
        ):
            assert hasattr(sr, name), (
                f"app.marketplace.skill_runtime missing public symbol {name!r}"
            )
