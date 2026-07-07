"""스킬 빌더 챗 전용 도구 (스펙 AD-3).

전부 기존 함수 재사용 — 검증은 ``validate_draft_package``(+호환성), 평가 생성은
``select_eval_template``/``generate_eval_cases``. 드래프트 파일 편집 자체는
deepagents 표준 FS 도구(``write_file``/``edit_file``)가 담당하고, 여기 도구는
드래프트 디렉토리를 어댑터로 읽는다.

DB 접근은 request-scoped 세션을 장수명 스트림에 고정하지 않도록 **세션 팩토리
클로저**로 받는다 (memory 도구 선례).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder.eval_case_generator import generate_eval_cases
from app.agent_runtime.skill_builder.eval_schema import (
    SkillEvalFile,
    SkillEvalSchemaError,
    parse_evals_json,
)
from app.agent_runtime.skill_builder.eval_templates import select_eval_template
from app.models.skill_builder_session import SkillBuilderSession
from app.schemas.skill_builder import SkillDraftFile
from app.services import skill_draft_workspace
from app.skills.validator import validate_draft_package
from app.tools.risk import ToolRiskLevel, attach_tool_risk, risk_metadata_dict

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

EVALS_FILE_PATH = "evals/evals.json"

# AD-4 — 세션 동의("이 세션에서 계속 허용")가 허용되는 도구. finalize_skill은
# 절대 포함 금지(항상 승인 카드), requires_network 드래프트는 런타임에서 재차
# 차단된다 (``skill_draft_workspace.draft_requires_network``).
SESSION_CONSENT_ELIGIBLE_TOOLS = frozenset({"test_skill_draft"})

# fabricated descriptor의 slug — 프론트매터에서 오는 LLM 저작 값이라 엄격히
# 새니타이즈한다 (materialize가 ``runtime_root / slug``로 복사하므로 경로 성분
# 이 섞이면 traversal).
_SAFE_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_FALLBACK_SLUG = "draft"


class _NoArgs(BaseModel):
    """인자 없는 도구용 빈 스키마."""


class _GenerateEvalsInput(BaseModel):
    intent: str | None = Field(
        default=None,
        max_length=2000,
        description=(
            "Optional one-line description of what the skill should do. "
            "Defaults to the session's original user request."
        ),
    )


class _TestSkillDraftInput(BaseModel):
    command: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description=(
            "Shell command to run inside the draft skill sandbox (e.g. "
            "'python scripts/run.py inputs/example.csv'). Only the allowlisted "
            "interpreters permitted by the skill execution policy will run."
        ),
    )


def _draft_slug(files: Sequence[SkillDraftFile]) -> str:
    """SKILL.md 프론트매터 ``name`` → 새니타이즈된 slug (실패 시 'draft')."""

    skill_md = next((f for f in files if f.path == "SKILL.md"), None)
    if skill_md is None:
        return _FALLBACK_SLUG
    from app.skills.inspector import SkillMetadataError, parse_skill_md

    try:
        parsed = parse_skill_md(skill_md.content, require_metadata=True)
    except SkillMetadataError:
        return _FALLBACK_SLUG
    raw = str(parsed["metadata"].get("name") or "").strip().lower()
    match = _SAFE_SLUG_RE.fullmatch(raw)
    return match.group(0) if match else _FALLBACK_SLUG


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def build_skill_builder_tools(
    *,
    session_id: str,
    workspace_path: str,
    session_factory: SessionFactory,
    user_id: str | None = None,
    agent_id: str | None = None,
    credential_subject_user_id: str | None = None,
    include_sandbox: bool = False,
) -> list[BaseTool]:
    """빌더 세션에 바인딩된 도구 리스트.

    ``include_sandbox=True`` 면 ``test_skill_draft``(저장 전 드래프트 샌드박스
    실행, CODE_EXECUTION risk)를 함께 붙인다 — 런타임 분기 전용. DB-free
    호출자(테스트 등)는 기본값으로 validate/generate만 받는다.
    """

    try:
        session_uuid = uuid.UUID(session_id)
    except (TypeError, ValueError):
        logger.warning("skill builder tools skipped — bad session id: %r", session_id)
        return []

    async def _load_session(db: AsyncSession) -> SkillBuilderSession | None:
        result = await db.execute(
            select(SkillBuilderSession).where(SkillBuilderSession.id == session_uuid)
        )
        return result.scalar_one_or_none()

    async def validate_skill() -> str:
        """드래프트를 검증하고 결과를 반환한다 (읽기 전용 + 세션에 결과 저장)."""

        try:
            files = skill_draft_workspace.load_draft_files(workspace_path)
            result = validate_draft_package(files=files)
        except Exception:  # noqa: BLE001 — 도구 에러는 모델에게 텍스트로 전달
            logger.exception("validate_skill failed (session=%s)", session_id)
            return _json_dumps(
                {"error": "validation failed unexpectedly; check draft files and retry"}
            )
        async with session_factory() as db:
            session = await _load_session(db)
            if session is not None:
                session.validation_result = result
                compatibility = result.get("compatibility_result")
                if isinstance(compatibility, dict):
                    session.compatibility_result = compatibility
                await db.commit()
        return _json_dumps({"session_id": session_id, **result})

    async def generate_evals(intent: str | None = None) -> str:
        """평가 케이스를 생성해 드래프트의 ``evals/evals.json``에 기록한다."""

        effective_intent = (intent or "").strip()
        if not effective_intent:
            async with session_factory() as db:
                session = await _load_session(db)
                effective_intent = (session.user_request if session else "") or "general task"

        skill_md = next(
            (
                f
                for f in skill_draft_workspace.load_draft_files(workspace_path)
                if f.path == "SKILL.md"
            ),
            None,
        )
        template = select_eval_template(
            intent=effective_intent,
            draft_package={"skill_md": skill_md.content} if skill_md else None,
        )
        cases = generate_eval_cases(intent=effective_intent, template=template)
        eval_file = SkillEvalFile(name=template.label, evals=cases)
        content = json.dumps(eval_file.model_dump(mode="json"), ensure_ascii=False, indent=2)
        try:
            parse_evals_json(content)  # 스키마 가드 — 기록 전 라운드트립 검증
        except SkillEvalSchemaError:
            logger.exception("generate_evals produced invalid schema (session=%s)", session_id)
            return _json_dumps({"error": "generated evals failed schema validation"})

        target = skill_draft_workspace.resolve_workspace_dir(workspace_path) / EVALS_FILE_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return _json_dumps(
            {
                "session_id": session_id,
                "path": EVALS_FILE_PATH,
                "template_key": template.key,
                "case_count": len(cases),
            }
        )

    async def test_skill_draft(command: str) -> str:
        """드래프트를 스킬 샌드박스에서 실행한다 (저장 전 시험, AD-3)."""

        from app.agent_runtime.skill_builder.eval_runner import run_eval_skill_command
        from app.config import settings
        from app.marketplace.skill_runtime import build_skill_runtime_context

        files = skill_draft_workspace.load_draft_files(workspace_path)
        slug = _draft_slug(files)
        execution_profile = skill_draft_workspace.draft_execution_profile(workspace_path)
        # fabricated descriptor — DB row 불요 (skill_evaluation_worker_state 선례).
        # ``agent_runtime_name=None`` 로 non-agent 런타임 루트 레이아웃을 강제해
        # run_eval_skill_command 의 slug 경로와 일치시킨다. thread_id 를 세션에
        # 고정하면 재실행이 같은 마운트를 wipe+copy 로 재사용하고, mtime 기반
        # runtime-root GC 가 자동으로 청소한다.
        sandbox_thread_id = f"skill-draft-{session_id}"
        fabricated_cfg = SimpleNamespace(
            thread_id=sandbox_thread_id,
            agent_runtime_name=None,
            agent_skills=[
                {
                    "id": session_id,
                    "slug": slug,
                    "name": slug,
                    "description": "skill draft under test",
                    "storage_path": workspace_path,
                    "execution_profile": execution_profile,
                }
            ],
            credential_subject_user_id=credential_subject_user_id,
            user_id=user_id,
            agent_id=agent_id,
        )
        try:
            data_dir = Path(settings.data_root)
            ctx = build_skill_runtime_context(
                fabricated_cfg,  # type: ignore[arg-type] — duck-typed cfg (선례 동일)
                data_dir=data_dir,
                output_root=data_dir / "skill-draft-runs",
            )
            ctx.audit_kind = "skill_builder.draft_test"
            ctx.run_id = session_id
            return await run_eval_skill_command(ctx, skill_slug=slug, command=command)
        except Exception:  # noqa: BLE001 — 도구 에러는 모델에게 텍스트로 전달
            logger.exception("test_skill_draft failed (session=%s)", session_id)
            return "Error: draft test execution failed unexpectedly."

    tools: list[BaseTool] = [
        StructuredTool.from_function(
            coroutine=validate_skill,
            name="validate_skill",
            description=(
                "Validate the current skill draft (SKILL.md metadata, references, "
                "scripts, secrets, portable compatibility). Read-only. Run this after "
                "meaningful edits and before proposing finalization."
            ),
            args_schema=_NoArgs,
        ),
        StructuredTool.from_function(
            coroutine=generate_evals,
            name="generate_evals",
            description=(
                "Generate evaluation cases for the draft and write them to "
                "evals/evals.json inside the draft workspace. Optionally pass a short "
                "intent description; defaults to the session's original request."
            ),
            args_schema=_GenerateEvalsInput,
        ),
    ]
    if include_sandbox:
        sandbox_tool = StructuredTool.from_function(
            coroutine=test_skill_draft,
            name="test_skill_draft",
            description=(
                "Run a command against the CURRENT draft in the skill sandbox "
                "(unsaved state). Use it to try the skill on the user's examples "
                "(files under inputs/). Requires user approval before running."
            ),
            args_schema=_TestSkillDraftInput,
        )
        # AD-4 — execute_in_skill 과 동일한 CODE_EXECUTION 위험 메타. 기본
        # interrupt 정책이 이 메타에서 승인 카드를 만든다.
        attach_tool_risk(
            sandbox_tool,
            risk_metadata_dict(
                ToolRiskLevel.CODE_EXECUTION,
                allowed_decisions=("approve", "reject"),
                trigger_safe=False,
                reason="test_skill_draft runs draft skill code in the sandbox",
            ),
        )
        tools.append(sandbox_tool)
    return tools


__all__ = ["EVALS_FILE_PATH", "build_skill_builder_tools"]
