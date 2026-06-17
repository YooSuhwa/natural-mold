from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.agent_runtime import skill_execution_policy
from app.agent_runtime.skill_builder.eval_schema import parse_evals_json
from app.agent_runtime.skill_executor import _create_skill_execute_tool
from app.marketplace.skill_runtime import SkillRuntimeDescriptor, SkillToolContext
from app.models.audit_event import AuditEvent
from app.schemas.skill_evaluation import SkillEvaluationSetCreate
from tests.conftest import TestSession

MAX_EXPECTED_EVAL_CASES = 50


@pytest.mark.asyncio
async def test_execute_in_skill_rejects_python_inline_code_before_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _ctx(tmp_path, slug="inline-python")
    tool = _create_skill_execute_tool(ctx)
    assert tool.coroutine is not None
    monkeypatch.setattr("app.agent_runtime.skill_executor_audit.async_session", TestSession)

    result = await tool.coroutine(
        skill_directory="/runtime/thread-sandbox/skills/inline-python/",
        command="python -c 'print(123)'",
    )
    event = await _sandbox_event("inline_python")

    assert result == "Error: python command must be `python scripts/<file>.py ...`."
    assert event.event_metadata is not None
    assert event.event_metadata["command_executable"] == "python"
    assert not ctx.output_dir.exists()


@pytest.mark.asyncio
async def test_execute_in_skill_rejects_private_curl_url_even_with_network_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _ctx(
        tmp_path,
        slug="curl-private",
        execution_profile={"requires_network": True},
    )
    tool = _create_skill_execute_tool(ctx)
    assert tool.coroutine is not None
    monkeypatch.setattr("app.agent_runtime.skill_executor_audit.async_session", TestSession)

    result = await tool.coroutine(
        skill_directory="/runtime/thread-sandbox/skills/curl-private/",
        command="curl http://169.254.169.254/latest/meta-data/iam/security-credentials",
    )
    event = await _sandbox_event("curl_url_policy")

    assert result == "Error: curl URL host is not allowed."
    assert event.event_metadata is not None
    assert event.event_metadata["command_executable"] == "curl"
    assert "169.254.169.254" not in str(event.event_metadata)
    assert not ctx.output_dir.exists()


@pytest.mark.asyncio
async def test_execute_in_skill_rejects_curl_file_url_outside_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _ctx(
        tmp_path,
        slug="curl-file",
        execution_profile={"requires_network": True},
    )
    tool = _create_skill_execute_tool(ctx)
    assert tool.coroutine is not None
    monkeypatch.setattr("app.agent_runtime.skill_executor_audit.async_session", TestSession)

    result = await tool.coroutine(
        skill_directory="/runtime/thread-sandbox/skills/curl-file/",
        command="curl file:///etc/passwd",
    )
    event = await _sandbox_event("curl_url_policy")

    assert result == "Error: curl URL host is not allowed."
    assert event.event_metadata is not None
    assert event.event_metadata["command_executable"] == "curl"
    assert "/etc/passwd" not in str(event.event_metadata)
    assert not ctx.output_dir.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://2130706433/",
        "http://0x7f000001/",
        "http://0177.0.0.1/",
        "http://localhost./",
    ],
)
async def test_execute_in_skill_rejects_nonstandard_loopback_curl_hosts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    ctx = _ctx(
        tmp_path,
        slug="curl-numeric-host",
        execution_profile={"requires_network": True},
    )
    tool = _create_skill_execute_tool(ctx)
    assert tool.coroutine is not None
    monkeypatch.setattr("app.agent_runtime.skill_executor_audit.async_session", TestSession)

    result = await tool.coroutine(
        skill_directory="/runtime/thread-sandbox/skills/curl-numeric-host/",
        command=f"curl {url}",
    )

    assert result == "Error: curl URL host is not allowed."
    assert not ctx.output_dir.exists()


@pytest.mark.asyncio
async def test_execute_in_skill_rejects_curl_redirect_following(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _ctx(
        tmp_path,
        slug="curl-redirect",
        execution_profile={"requires_network": True},
    )
    tool = _create_skill_execute_tool(ctx)
    assert tool.coroutine is not None
    monkeypatch.setattr("app.agent_runtime.skill_executor_audit.async_session", TestSession)

    result = await tool.coroutine(
        skill_directory="/runtime/thread-sandbox/skills/curl-redirect/",
        command="curl -L https://example.com/",
    )

    assert result == "Error: curl option is not allowed."
    assert not ctx.output_dir.exists()


def test_curl_policy_pins_public_dns_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(*_args: object, **_kwargs: object):
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(skill_execution_policy.socket, "getaddrinfo", fake_getaddrinfo)

    args, error = skill_execution_policy._prepare_skill_subprocess_args(
        "curl https://example.com/path",
        resolved=Path("/tmp/skill"),
        env={},
    )

    assert error is None
    assert args is not None
    assert "--resolve" in args
    assert "example.com:443:93.184.216.34" in args


def test_curl_policy_formats_public_ipv6_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(*_args: object, **_kwargs: object):
        return [(10, 1, 6, "", ("2606:2800:220:1:248:1893:25c8:1946", 443, 0, 0))]

    monkeypatch.setattr(skill_execution_policy.socket, "getaddrinfo", fake_getaddrinfo)

    args, error = skill_execution_policy._prepare_skill_subprocess_args(
        "curl https://example.com/path",
        resolved=Path("/tmp/skill"),
        env={},
    )

    assert error is None
    assert args is not None
    assert "example.com:443:[2606:2800:220:1:248:1893:25c8:1946]" in args


def test_curl_policy_rejects_dns_resolution_to_private_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(*_args: object, **_kwargs: object):
        return [(2, 1, 6, "", ("10.1.2.3", 443))]

    monkeypatch.setattr(skill_execution_policy.socket, "getaddrinfo", fake_getaddrinfo)

    args, error = skill_execution_policy._prepare_skill_subprocess_args(
        "curl https://private.example/path",
        resolved=Path("/tmp/skill"),
        env={},
    )

    assert args is None
    assert error == "Error: curl URL host is not allowed."


def test_manual_evaluation_sets_have_case_count_limit() -> None:
    evals = [{"input": f"case-{index}"} for index in range(MAX_EXPECTED_EVAL_CASES + 1)]

    with pytest.raises(ValidationError):
        SkillEvaluationSetCreate(name="Too many", evals=evals)


def test_manual_evaluation_sets_reject_empty_case_list() -> None:
    with pytest.raises(ValidationError):
        SkillEvaluationSetCreate(name="Empty", evals=[])


def test_package_evals_file_has_case_count_limit() -> None:
    evals = [{"input": f"case-{index}"} for index in range(MAX_EXPECTED_EVAL_CASES + 1)]

    with pytest.raises(ValueError, match="invalid evals/evals.json schema"):
        parse_evals_json(json.dumps({"evals": evals}))


def _ctx(
    tmp_path: Path,
    *,
    slug: str,
    execution_profile: dict[str, bool] | None = None,
) -> SkillToolContext:
    runtime_root = tmp_path / "runtime"
    skill_dir = runtime_root / slug
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "run.py").write_text("print('ok')\n")
    descriptor = SkillRuntimeDescriptor(
        id=uuid.uuid4(),
        slug=slug,
        name=slug.title(),
        description="sandbox probe",
        original_storage_path=skill_dir,
        runtime_storage_path=skill_dir,
        execution_profile=execution_profile,
    )
    return SkillToolContext(
        thread_id="thread-sandbox",
        output_dir=tmp_path / "outputs",
        runtime_root=runtime_root,
        descriptors={slug: descriptor},
        run_id="run-sandbox",
    )


async def _sandbox_event(reason_code: str) -> AuditEvent:
    async with TestSession() as db:
        return (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.action == "skill_security.sandbox_denied",
                    AuditEvent.reason_code == reason_code,
                )
            )
        ).scalar_one()
