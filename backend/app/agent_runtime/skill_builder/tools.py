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
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
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
from app.services import skill_draft_workspace
from app.skills.validator import validate_draft_package

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

EVALS_FILE_PATH = "evals/evals.json"


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


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def build_skill_builder_tools(
    *,
    session_id: str,
    workspace_path: str,
    session_factory: SessionFactory,
) -> list[BaseTool]:
    """빌더 세션에 바인딩된 도구 리스트 (M3: validate_skill, generate_evals)."""

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
        content = json.dumps(
            eval_file.model_dump(mode="json"), ensure_ascii=False, indent=2
        )
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

    return [
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


__all__ = ["EVALS_FILE_PATH", "build_skill_builder_tools"]
