from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path

from langchain_core.tools import BaseTool, StructuredTool

from app.agent_runtime import skill_sandbox
from app.agent_runtime.skill_execution_policy import (
    _expand_shell_vars,
    _prepare_skill_subprocess_args,
    _requires_network,
    _resolve_skill_node_modules,
    _skill_timeout_seconds,
    sandbox_denial_for_prepare_error,
    skill_timeout_policy_error,
)
from app.agent_runtime.skill_executor_audit import (
    record_credential_audits,
    record_sandbox_denial,
    record_sandbox_run,
)
from app.agent_runtime.skill_sandbox import SkillRunResult, SkillSandboxError
from app.config import settings
from app.marketplace.skill_runtime import (
    ResolvedCredential,
    SkillRuntimeDescriptor,
    SkillToolContext,
)
from app.tools.keys import attach_parts, skill_ref
from app.tools.risk import attach_tool_risk, execute_in_skill_risk

__all__ = [
    "_create_skill_execute_tool",
    "_expand_shell_vars",
    "_prepare_skill_subprocess_args",
    "_skill_timeout_seconds",
]


async def _run_on_host(
    args: list[str], *, cwd: Path, env: dict[str, str], timeout_seconds: float
) -> SkillRunResult:
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return SkillRunResult(stdout="", stderr="", returncode=None, timed_out=True)
    except asyncio.CancelledError:
        # run cancel(Stop)/worker shutdown 이 이 await 를 취소하면 timeout
        # kill 경로도 함께 취소되어 subprocess 가 고아로 남는다 — 즉시
        # 종료시켜 취소된 대화 디렉토리에 출력이 계속 쌓이는 것을 막고
        # 취소를 전파한다.
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise
    return SkillRunResult(
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
        returncode=proc.returncode,
    )


async def _run_sandboxed(
    ctx: SkillToolContext,
    descriptor: SkillRuntimeDescriptor,
    args: list[str],
    *,
    skill_dir: Path,
    env: dict[str, str],
    timeout_seconds: float,
) -> SkillRunResult:
    """The sandbox run plus its audit event and trace span (plan v2 §15.3)."""
    domains = skill_sandbox.network_domains(descriptor.execution_profile)
    details: dict[str, object] = {
        "command_executable": Path(args[0]).name,
        "network_domains": list(domains),
        "timeout_seconds": timeout_seconds,
    }
    started = time.monotonic()

    async def audit(outcome: str, reason: str | None, **extra: object) -> None:
        extra["duration_ms"] = int((time.monotonic() - started) * 1000)
        await record_sandbox_run(
            ctx, descriptor, outcome=outcome, reason_code=reason, details={**details, **extra}
        )

    with skill_sandbox.trace_span(ctx.run_id, {"skill_slug": descriptor.slug, **details}) as span:
        try:
            run = await skill_sandbox.run_in_sandbox(
                args,
                skill_dir=skill_dir,
                output_dir=ctx.output_dir,
                env=env,
                timeout_seconds=timeout_seconds,
                domains=domains,
            )
        except SkillSandboxError as exc:
            await audit("failure", "sandbox_unavailable", error=str(exc))
            raise
        except asyncio.CancelledError:
            await asyncio.shield(audit("failure", "cancelled"))
            raise
        result = {"sandbox_id": run.sandbox_id, "exit_code": run.returncode}
        if span is not None:
            span.update(output={**result, "timed_out": run.timed_out})
    if run.timed_out:
        await audit("failure", "timeout", **result)
    elif run.returncode != 0:
        await audit("failure", "nonzero_exit", **result)
    else:
        await audit("success", None, **result)
    return run


def _create_skill_execute_tool(ctx: SkillToolContext) -> BaseTool:
    """스킬 디렉토리에서 Python 스크립트를 실행하는 도구를 생성.

    ADR-017 Slice E refactor — the tool now closes over a
    ``SkillToolContext`` (output_dir + thread_id + runtime_root + slug
    descriptor map). Stage 1 preserves the legacy validation surface;
    stage 2 swaps ``runtime_root`` to the per-thread location and adds
    "unknown slug" rejection. The closure shape was deliberately
    chosen to avoid an argument explosion on the inner ``execute_in_skill``
    body — see Bezos OI-3.
    """

    output_dir = ctx.output_dir
    api_file_prefix = f"/api/conversations/{ctx.thread_id}/files/" if ctx.thread_id else ""
    _path_re = re.compile(re.escape(str(output_dir)) + r"/([^\s\n]+)") if api_file_prefix else None

    async def execute_in_skill(skill_directory: str, command: str) -> str:
        """스킬 디렉토리에서 Python 스크립트를 실행합니다.

        Args:
            skill_directory: 스킬 디렉토리의 가상 경로
                (예: /runtime/<thread_id>/skills/<slug>/).
            command: 실행할 명령어 (예: python scripts/mark_seat.py search 이상윤)
        """
        # ``Path(skill_directory).name`` extracts the final segment
        # regardless of leading slashes / trailing slashes — the LLM may
        # pass any of ``/skills/<slug>``, ``<slug>``, ``<slug>/``.
        requested_slug = Path(skill_directory.strip("/")).name
        descriptor = ctx.descriptors.get(requested_slug)
        if descriptor is None:
            # Selected-skill mount (Spec §9) — even if the slug resolves
            # to a real on-disk directory, refuse it when it wasn't
            # attached to this agent. This is the regression guard for
            # the legacy broad ``/skills/`` mount that leaked siblings.
            return f"Error: skill not attached to this agent: {requested_slug}"

        # Resolve against the per-thread runtime root so traversal
        # attempts (``../``, absolute paths like ``/etc/passwd``) all
        # fail the ``is_relative_to`` check below.
        resolved = descriptor.runtime_storage_path.resolve()
        if not resolved.is_relative_to(ctx.runtime_root.resolve()) or not resolved.is_dir():
            return f"Error: invalid skill directory: {skill_directory}"

        # A sandboxed command sees its own copies at fixed paths and the image's PATH.
        sandboxed = settings.skill_sandbox_enabled
        if sandboxed and descriptor.slug in skill_sandbox.HOST_WORKSPACE_SKILLS:
            await record_sandbox_denial(
                ctx, descriptor, reason_code="sandbox_unsupported", executable="unknown"
            )
            return (
                "Error: this skill keeps a workspace outside the conversation, which the "
                "skill sandbox does not support yet."
            )
        out = skill_sandbox.OUTPUT_DIR if sandboxed else str(output_dir)
        skill_home = skill_sandbox.SKILL_DIR if sandboxed else str(resolved)
        env = {
            "PYTHONPATH": skill_home,
            "HOME": skill_home,
            "SKILL_OUTPUT_DIR": out,
            "OUTPUTS_DIR": out,
        }
        if not sandboxed:
            env["PATH"] = os.environ.get("PATH", "/usr/bin:/usr/local/bin")
            node_modules = _resolve_skill_node_modules()
            if node_modules is not None:
                env["NODE_PATH"] = str(node_modules)
            for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
                value = os.environ.get(key)
                if value:
                    env[key] = value
        # Slice E Stage 3 — credential env injection (Spec §8.2).
        # ``descriptor.credential_bindings`` is populated by
        # ``build_skill_runtime_context`` at agent build time so the
        # hot path here only does an in-memory copy; no decrypt, no DB.
        # ``env_map`` shape: ``{credential_field_name: env_var_name}``.
        injected_env: dict[str, str] = {}
        injected_credentials: list[tuple[str, ResolvedCredential]] = []
        for requirement_key, rc in descriptor.credential_bindings.items():
            injected = False
            for field, env_name in rc.env_map.items():
                value = rc.decrypted.get(field)
                if value is None:
                    continue
                env[env_name] = value
                injected_env[env_name] = value
                injected = True
            if injected:
                injected_credentials.append((requirement_key, rc))

        args, error = _prepare_skill_subprocess_args(command, resolved=resolved, env=env)
        if error is not None or args is None:
            denial = sandbox_denial_for_prepare_error(command, error or "")
            if denial is not None:
                reason_code, denied_executable = denial
                await record_sandbox_denial(
                    ctx,
                    descriptor,
                    reason_code=reason_code,
                    executable=denied_executable,
                )
            return error or "Error: invalid command."
        timeout_error = skill_timeout_policy_error(descriptor)
        if timeout_error is not None:
            await record_sandbox_denial(
                ctx,
                descriptor,
                reason_code="timeout_policy",
                executable=Path(args[0]).name,
            )
            return timeout_error
        timeout_seconds = _skill_timeout_seconds(descriptor)
        executable = Path(args[0]).name
        if executable == "curl" and not _requires_network(descriptor):
            await record_sandbox_denial(
                ctx,
                descriptor,
                reason_code="undeclared_network",
                executable=executable,
            )
            return "Error: network access requires execution_profile.requires_network=true."
        await record_credential_audits(
            ctx,
            descriptor,
            injected_credentials=injected_credentials,
            executable=executable,
            timeout_seconds=timeout_seconds,
        )

        await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
        if sandboxed:
            try:
                run = await _run_sandboxed(
                    ctx,
                    descriptor,
                    args,
                    skill_dir=resolved,
                    env=env,
                    timeout_seconds=timeout_seconds,
                )
            except SkillSandboxError as exc:
                return f"Error: {exc}"
        else:
            run = await _run_on_host(args, cwd=resolved, env=env, timeout_seconds=timeout_seconds)
        if run.timed_out:
            return f"Error: script execution timed out ({timeout_seconds:g}s)."

        result = run.stdout
        if run.returncode != 0:
            result += f"\nSTDERR: {run.stderr}"
        if sandboxed:
            # Copied-back outputs live in output_dir; the rewrite below expects that path.
            result = result.replace(f"{skill_sandbox.OUTPUT_DIR}/", f"{output_dir}/")

        # Phase 3 skill-axis usage (spec §5.3 — "성공 경로에서") — count a
        # SUCCESSFUL (exit 0) sandbox execution for agent runs (chat + scheduled
        # triggers + Agent-API; all use the default "execute_in_skill"
        # audit_kind). Evaluation runs ("skill_evaluation") and builder draft
        # tests ("skill_builder.draft_test") set a different audit_kind — and
        # drafts carry no persisted skill row — so they never reach this ledger.
        # Trigger/API runs whose thread_id is not a conversation UUID record with
        # conversation_id NULL (self-describing). A failed script (nonzero exit)
        # is NOT counted — the "실행" stat reflects successful executions.
        if run.returncode == 0 and ctx.audit_kind == "execute_in_skill" and ctx.user_id is not None:
            from app.services.skill_usage_service import record_chat_execution_nonfatal

            await record_chat_execution_nonfatal(
                skill_id=descriptor.id,
                user_id=ctx.user_id,
                thread_id=ctx.thread_id,
                agent_id=ctx.agent_id,
                tenant_id=ctx.tenant_id,
            )

        # IMAGE: 절대경로 → API URL 자동 변환
        if _path_re and str(output_dir) in result:
            result = _path_re.sub(lambda m: api_file_prefix + m.group(1), result)

        # 출력 파일 수집
        def _collect_outputs() -> list[str]:
            if output_dir.exists():
                return [f.name for f in output_dir.iterdir() if f.is_file()]
            return []

        files = await asyncio.to_thread(_collect_outputs)
        if files:
            result += "\n\nOUTPUT_FILES: " + ", ".join(files)

        # Slice E Stage 4 — redact credential values that the script
        # may have echoed back through stdout/stderr (debug prints,
        # uncaught exceptions, etc.). The mapped env dict is the
        # authoritative set of values the runtime injected for this
        # skill; everything else passes through untouched.
        if injected_env:
            from app.marketplace.redaction import redact_credential_values

            result = redact_credential_values(result, injected_env)

        return result

    tool = StructuredTool.from_function(
        coroutine=execute_in_skill,
        name="execute_in_skill",
        description=(
            "Execute an allowed Python or Node script inside a skill directory. "
            "Use this when SKILL.md instructs you to run a script. "
            "Output files (images etc.) will be in OUTPUT_FILES."
        ),
    )
    # Which skill a call means decides whether it asks (``app.approvals.tool_modes``).
    attach_parts(
        tool,
        {
            slug: skill_ref(
                {
                    "id": found.id,
                    "slug": found.slug,
                    "name": found.name,
                    "approval_default": found.approval_default,
                }
            )
            for slug, found in ctx.descriptors.items()
        },
    )
    return attach_tool_risk(tool, execute_in_skill_risk())
