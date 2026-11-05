"""``execute_in_skill`` through the sandbox runner (SKILL_SANDBOX_ENABLED), with a fake sandbox.

The live server check (real OpenSandbox, docker runtime) is in
docs/design-docs/skill-sandbox-docker.md; these lock the contract the executor keeps.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from app.agent_runtime import skill_executor, skill_sandbox
from app.agent_runtime.skill_executor import _create_skill_execute_tool
from app.config import settings
from app.marketplace.skill_runtime import (
    ResolvedCredential,
    SkillRuntimeDescriptor,
    SkillToolContext,
)
from app.models.audit_event import AuditEvent
from tests.conftest import TestSession

pytestmark = pytest.mark.asyncio
SECRET = "sk-sandbox-secret-value"


def _lines(text: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(text=line) for line in text.splitlines()]


def _execution(code: int | None, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        exit_code=code,
        logs=SimpleNamespace(stdout=_lines(stdout), stderr=_lines(stderr)),
        error=None,
    )


@dataclass
class FakeSandbox:
    """Records what the runner sends; ``outputs`` is what the command "wrote"."""

    run_code: int | None = 0
    run_stdout: str = ""
    outputs: dict[str, bytes] = field(default_factory=dict)
    listing: str | None = None
    hang: bool = False
    # Set when a hanging command has started, so a test cancels only once it is in flight.
    hanging: asyncio.Event = field(default_factory=asyncio.Event)
    id: str = "fake-sandbox"
    written: dict[str, bytes] = field(default_factory=dict)
    commands_seen: list[tuple[str, Any]] = field(default_factory=list)
    killed: bool = False
    closed: bool = False

    def __post_init__(self) -> None:
        self.files = SimpleNamespace(write_files=self._write_files, read_bytes=self._read_bytes)
        self.commands = SimpleNamespace(run=self._run)

    async def _write_files(self, entries: list[Any]) -> None:
        for entry in entries:
            self.written[entry.path] = entry.data

    async def _read_bytes(self, path: str) -> bytes:
        return self.outputs[path.rsplit("/", 1)[1]]

    async def _run(self, command: str, opts: Any = None) -> SimpleNamespace:
        self.commands_seen.append((command, opts))
        if command.startswith("mkdir"):
            return _execution(0)
        if command.startswith("find"):
            listing = self.listing
            if listing is None:
                listing = "".join(f"{n}\t{len(c)}\n" for n, c in self.outputs.items())
            return _execution(0, listing)
        if self.hang:
            self.hanging.set()
            await asyncio.sleep(3600)
        return _execution(self.run_code, self.run_stdout)

    async def kill(self) -> None:
        self.killed = True

    async def close(self) -> None:
        self.closed = True

    @property
    def user_command(self) -> tuple[str, Any]:
        return next(c for c in self.commands_seen if not c[0].startswith(("mkdir", "find")))


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSandbox:
    sandbox = FakeSandbox()
    created: list[tuple[float, tuple[str, ...]]] = []

    async def create(timeout_seconds: float, domains: tuple[str, ...]) -> FakeSandbox:
        created.append((timeout_seconds, tuple(domains)))
        return sandbox

    monkeypatch.setattr("app.agent_runtime.skill_executor_audit.async_session", TestSession)
    monkeypatch.setattr(settings, "skill_sandbox_enabled", True)
    monkeypatch.setattr(settings, "skill_sandbox_allowed_domains", "")
    monkeypatch.setattr(skill_sandbox, "_create", create)
    sandbox.created = created  # type: ignore[attr-defined]

    async def no_host(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a sandboxed command must not run on the host")

    async def no_audit(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(skill_executor.asyncio, "create_subprocess_exec", no_host)
    monkeypatch.setattr(skill_executor, "record_credential_audits", no_audit)
    return sandbox


def _tool(
    tmp_path: Path, profile: dict[str, Any] | None = None, slug: str = "demo"
) -> tuple[Any, Path]:
    skill_dir = tmp_path / "runtime" / slug
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "run.py").write_text("print('hi')\n")
    (skill_dir / "SKILL.md").write_text("---\nname: demo\n---\n")
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    (output_dir / "earlier.txt").write_text("from an earlier turn")
    descriptor = SkillRuntimeDescriptor(
        id=uuid.uuid4(),
        slug=slug,
        name="Demo",
        description="demo",
        original_storage_path=skill_dir,
        runtime_storage_path=skill_dir,
        execution_profile=profile,
        credential_bindings={
            "openai": ResolvedCredential(
                credential_id=uuid.uuid4(),
                definition_key="openai",
                env_map={"api_key": "OPENAI_API_KEY"},
                decrypted={"api_key": SECRET},
            )
        },
    )
    ctx = SkillToolContext(
        thread_id="thread-1",
        tenant_id=uuid.uuid4(),
        output_dir=output_dir,
        runtime_root=tmp_path / "runtime",
        descriptors={slug: descriptor},
        run_id="run-1",
    )
    return _create_skill_execute_tool(ctx), output_dir


async def _invoke(
    tool: Any,
    command: str = "python scripts/run.py --out $OUTPUTS_DIR/a.png",
    slug: str = "demo",
) -> str:
    return await tool.ainvoke({"skill_directory": f"/skills/{slug}", "command": command})


async def test_command_runs_in_the_sandbox_and_its_outputs_come_back(
    fake: FakeSandbox, tmp_path: Path
) -> None:
    fake.run_stdout = f"saved /workspace/outputs/a.png\nkey={SECRET}"
    fake.outputs = {"a.png": b"png-bytes"}
    tool, output_dir = _tool(tmp_path)

    result = await _invoke(tool)

    command, opts = fake.user_command
    assert command == "python scripts/run.py --out /workspace/outputs/a.png"
    assert (opts.uid, opts.gid, opts.working_directory) == (1000, 1000, "/workspace/skill")
    assert opts.envs["OPENAI_API_KEY"] == SECRET  # per command, never at create time
    assert opts.envs["OUTPUTS_DIR"] == "/workspace/outputs" and "PATH" not in opts.envs
    assert fake.written["/workspace/skill/scripts/run.py"] == b"print('hi')\n"
    assert fake.written["/workspace/outputs/earlier.txt"] == b"from an earlier turn"
    assert (output_dir / "a.png").read_bytes() == b"png-bytes"
    assert "/api/conversations/thread-1/files/a.png" in result
    assert SECRET not in result
    assert "OUTPUT_FILES:" in result and "a.png" in result
    assert fake.created == [(30.0, ())]  # type: ignore[attr-defined]
    assert fake.killed and fake.closed
    event = await _run_event()
    assert (event.outcome, event.reason_code) == ("success", None)
    assert event.event_metadata is not None
    assert event.event_metadata["sandbox_id"] == "fake-sandbox"
    assert event.event_metadata["exit_code"] == 0
    assert event.event_metadata["network_domains"] == []
    assert SECRET not in str(event.event_metadata)


async def _run_event() -> AuditEvent:
    async with TestSession() as db:
        return (
            await db.execute(
                select(AuditEvent).where(AuditEvent.action == "skill_security.sandbox_run")
            )
        ).scalar_one()


async def test_arguments_are_quoted_for_the_sandbox_shell(
    fake: FakeSandbox, tmp_path: Path
) -> None:
    tool, _ = _tool(tmp_path)

    await _invoke(tool, "python scripts/run.py 'a b; rm -rf /'")

    assert fake.user_command[0] == "python scripts/run.py 'a b; rm -rf /'"


async def test_unsafe_or_oversized_output_names_are_not_copied(
    fake: FakeSandbox, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(skill_sandbox, "MAX_OUTPUT_FILE_BYTES", 4)
    fake.outputs = {"ok.txt": b"ok", "big.bin": b"too-big"}
    fake.listing = "../escape.txt\t2\nok.txt\t2\n.\t1\nbig.bin\t7\n"
    tool, output_dir = _tool(tmp_path)

    await _invoke(tool)

    assert sorted(p.name for p in output_dir.iterdir()) == ["earlier.txt", "ok.txt"]
    assert not (tmp_path / "escape.txt").exists()


async def test_timeout_is_reported_and_the_sandbox_deleted(
    fake: FakeSandbox, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake.run_code = -1
    clock = iter([0.0, 31.0])
    monkeypatch.setattr(
        skill_sandbox.asyncio,
        "get_running_loop",
        lambda: SimpleNamespace(time=lambda: next(clock)),
    )
    tool, _ = _tool(tmp_path)

    assert await _invoke(tool) == "Error: script execution timed out (30s)."
    assert fake.killed
    assert not any(c[0].startswith("find") for c in fake.commands_seen)
    event = await _run_event()
    assert (event.outcome, event.reason_code) == ("failure", "timeout")


async def test_server_failure_fails_closed(
    fake: FakeSandbox, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opensandbox.exceptions import SandboxException

    async def unavailable(*_args: object) -> None:
        raise SandboxException("connection refused")

    monkeypatch.setattr(skill_sandbox, "_create", unavailable)
    tool, _ = _tool(tmp_path)

    assert await _invoke(tool) == "Error: skill sandbox is unavailable."
    event = await _run_event()
    assert (event.outcome, event.reason_code) == ("failure", "sandbox_unavailable")


async def test_a_cancelled_run_still_deletes_its_sandbox(fake: FakeSandbox, tmp_path: Path) -> None:
    fake.hang = True
    tool, _ = _tool(tmp_path)
    task = asyncio.create_task(_invoke(tool))
    # Wait for the command itself, not a fixed number of loop turns: the run first does real
    # IO (database, thread offload) that a busy CI runner can make slower than any count.
    hanging = asyncio.create_task(fake.hanging.wait())
    await asyncio.wait({task, hanging}, timeout=30, return_when=asyncio.FIRST_COMPLETED)
    hanging.cancel()
    assert fake.hanging.is_set(), task.result() if task.done() else "command never started"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert fake.killed
    event = await _run_event()
    assert (event.outcome, event.reason_code) == ("failure", "cancelled")


async def test_network_reaches_only_declared_and_allow_listed_domains(
    fake: FakeSandbox, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "skill_sandbox_allowed_domains", "pypi.org, Bad Domain")
    profile = {"requires_network": True, "network_domains": ["api.example.com", "*.cdn.io"]}
    tool, _ = _tool(tmp_path, profile)
    await _invoke(tool)

    domains = ("*.cdn.io", "api.example.com", "pypi.org")
    assert fake.created == [(30.0, domains)]  # type: ignore[attr-defined]
    event = await _run_event()
    assert event.event_metadata is not None
    assert event.event_metadata["network_domains"] == list(domains)


async def test_network_policy_denies_by_default_and_internal_ranges_first() -> None:
    assert skill_sandbox.network_domains({"network_domains": ["api.example.com"]}) == ()
    assert skill_sandbox.network_domains({"requires_network": True}) == ()
    closed = skill_sandbox.network_policy(())
    assert (closed.default_action, closed.egress) == ("deny", [])

    policy = skill_sandbox.network_policy(("api.example.com",))
    assert policy.default_action == "deny"
    rules = [(rule.action, rule.target) for rule in policy.egress or []]
    assert rules[-1] == ("allow", "api.example.com")
    denied = {target for action, target in rules[:-1] if action == "deny"}
    assert {"172.16.0.0/12", "10.0.0.0/8", "169.254.0.0/16", "127.0.0.0/8"} <= denied


async def test_a_skill_with_a_shared_workspace_is_refused_in_the_sandbox(
    fake: FakeSandbox, tmp_path: Path
) -> None:
    tool, _ = _tool(tmp_path, slug="openwiki")

    result = await _invoke(tool, slug="openwiki")

    assert result.startswith("Error: this skill keeps a workspace outside the conversation")
    assert fake.commands_seen == [] and fake.created == []  # type: ignore[attr-defined]
    async with TestSession() as db:
        denial = (
            await db.execute(
                select(AuditEvent).where(AuditEvent.action == "skill_security.sandbox_denied")
            )
        ).scalar_one()
    assert denial.reason_code == "sandbox_unsupported"


async def test_trace_span_attaches_to_the_run_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.observability import langfuse

    calls: list[dict[str, Any]] = []

    class Client:
        def start_as_current_observation(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return contextlib.nullcontext("span")

    monkeypatch.setattr(langfuse, "is_langfuse_enabled", lambda: True)
    monkeypatch.setattr(langfuse, "_get_langfuse_client", Client)
    monkeypatch.setattr(langfuse, "_trace_id_for_run", lambda _client, run_id: f"t-{run_id}")

    with skill_sandbox.trace_span("run-1", {"skill_slug": "demo"}) as span:
        assert span == "span"
    with skill_sandbox.trace_span(None, {}) as span:
        assert span is None

    assert calls == [
        {
            "as_type": "span",
            "name": "sandbox",
            "trace_context": {"trace_id": "t-run-1"},
            "metadata": {"skill_slug": "demo"},
        }
    ]
