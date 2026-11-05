"""``execute_in_skill`` through the sandbox runner (SKILL_SANDBOX_ENABLED), with a fake sandbox.

The live server check (real OpenSandbox, docker runtime) is in
docs/design-docs/skill-sandbox-docker.md; these lock the contract the executor keeps.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent_runtime import skill_executor, skill_sandbox
from app.agent_runtime.skill_executor import _create_skill_execute_tool
from app.config import settings
from app.marketplace.skill_runtime import (
    ResolvedCredential,
    SkillRuntimeDescriptor,
    SkillToolContext,
)

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
    created: list[tuple[float, bool]] = []

    async def create(timeout_seconds: float, requires_network: bool) -> FakeSandbox:
        created.append((timeout_seconds, requires_network))
        return sandbox

    monkeypatch.setattr(settings, "skill_sandbox_enabled", True)
    monkeypatch.setattr(skill_sandbox, "_create", create)
    sandbox.created = created  # type: ignore[attr-defined]

    async def no_host(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a sandboxed command must not run on the host")

    async def no_audit(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(skill_executor.asyncio, "create_subprocess_exec", no_host)
    monkeypatch.setattr(skill_executor, "record_credential_audits", no_audit)
    return sandbox


def _tool(tmp_path: Path, profile: dict[str, Any] | None = None) -> tuple[Any, Path]:
    skill_dir = tmp_path / "runtime" / "demo"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "run.py").write_text("print('hi')\n")
    (skill_dir / "SKILL.md").write_text("---\nname: demo\n---\n")
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    (output_dir / "earlier.txt").write_text("from an earlier turn")
    descriptor = SkillRuntimeDescriptor(
        id=uuid.uuid4(),
        slug="demo",
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
        descriptors={"demo": descriptor},
    )
    return _create_skill_execute_tool(ctx), output_dir


async def _invoke(
    tool: Any, command: str = "python scripts/run.py --out $OUTPUTS_DIR/a.png"
) -> str:
    return await tool.ainvoke({"skill_directory": "/skills/demo", "command": command})


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
    assert fake.created == [(30.0, False)]  # type: ignore[attr-defined]
    assert fake.killed and fake.closed


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


async def test_server_failure_fails_closed(
    fake: FakeSandbox, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opensandbox.exceptions import SandboxException

    async def unavailable(*_args: object) -> None:
        raise SandboxException("connection refused")

    monkeypatch.setattr(skill_sandbox, "_create", unavailable)
    tool, _ = _tool(tmp_path)

    assert await _invoke(tool) == "Error: skill sandbox is unavailable."


async def test_a_cancelled_run_still_deletes_its_sandbox(fake: FakeSandbox, tmp_path: Path) -> None:
    fake.hang = True
    tool, _ = _tool(tmp_path)
    task = asyncio.create_task(_invoke(tool))
    for _ in range(1000):
        if task.done() or any(not c[0].startswith("mkdir") for c in fake.commands_seen):
            break
        await asyncio.sleep(0)
    assert not task.done(), task.result()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert fake.killed


async def test_network_is_denied_unless_declared_and_internal_ranges_always(
    fake: FakeSandbox, tmp_path: Path
) -> None:
    tool, _ = _tool(tmp_path, {"requires_network": True})
    await _invoke(tool)
    assert fake.created == [(30.0, True)]  # type: ignore[attr-defined]

    closed = skill_sandbox.network_policy(False)
    assert (closed.default_action, closed.egress) == ("deny", [])
    open_ = skill_sandbox.network_policy(True)
    assert open_.default_action == "allow"
    denied = {rule.target for rule in open_.egress or [] if rule.action == "deny"}
    assert {"172.16.0.0/12", "10.0.0.0/8", "169.254.0.0/16", "127.0.0.0/8"} <= denied
