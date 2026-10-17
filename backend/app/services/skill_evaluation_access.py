"""``SkillEvaluationAccessPlanner``: the skill evaluation run's decisions (plan 11.0; F-13).

An evaluation run executes a skill's scripts with credentials outside any request, so
the worker re-decides it when it executes, not only when the run was created:

* the skill is the evaluation creator's private row in its persisted organization
  (``resolve_private_actor``: membership, company and account still active);
* the creator still holds ``skill#can_edit`` on it (enforced types only; legacy and
  shadow keep the owner-scoped load as the answer, shadow samples it);
* credentials are the creator's own skill bindings (``resolve_runtime_credentials`` in
  the creator's scope, private rows only), and their values feed the run's secret
  redaction (``SkillToolContext`` secret set) as before.

A denial is a ``SkillEvaluationExecutionError``: the worker fails the run before the
evaluator starts. An undecidable run (OpenFGA down, the process service not running) is
``SkillEvaluationAuthzUnavailable``: failed with the retryable ``authz_unavailable`` code
and audit ``reason_code=AUTHZ_UNAVAILABLE`` (the in-memory queue has no requeue; the
user re-runs it), never an unhandled error.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_config import AgentConfig
from app.authz import runtime as authz_runtime
from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.authz.scoping import private_scoped
from app.authz.service import AuthzService
from app.authz.service_types import Verdict
from app.authz.skill_access import SKILL_MIN_VISIBLE
from app.config import settings
from app.exceptions import AppError
from app.marketplace.skill_runtime import (
    SkillToolContext,
    build_skill_runtime_context,
    resolve_runtime_credentials,
)
from app.models.skill import Skill
from app.models.skill_evaluation import SkillEvaluationRun
from app.services.skill_evaluation_worker_types import (
    SkillEvaluationAuthzUnavailable,
    SkillEvaluationExecutionError,
)
from app.skills import service as skill_service

EVALUATE_PERMISSION = "can_edit"


@dataclass(frozen=True, slots=True)
class AuthorizedEvaluationSkill:
    skill: Skill
    organization: OrgContext


class SkillEvaluationAccessPlanner:
    def __init__(self, authz: AuthzService | None = None) -> None:
        self.authz = authz

    def _service(self) -> AuthzService:
        if self.authz is None:
            try:
                self.authz = authz_runtime.runtime_authz()
            except RuntimeError:  # the process service is not running
                raise SkillEvaluationAuthzUnavailable from None
        return self.authz

    async def authorize(
        self, db: AsyncSession, run: SkillEvaluationRun
    ) -> AuthorizedEvaluationSkill:
        target = await _load_private_skill(db, run)
        try:
            await self._service().require(
                Principal.for_user(target.organization),
                EVALUATE_PERMISSION,
                f"skill:{run.skill_id}",
                min_visible=SKILL_MIN_VISIBLE,
                resource_org_id=target.skill.org_id,
                legacy=Verdict.ALLOW,  # the owner-scoped load above
            )
        except AppError as denied:
            if denied.code == "authz_unavailable":
                raise SkillEvaluationAuthzUnavailable from None
            raise SkillEvaluationExecutionError(denied.code) from None
        return target

    async def runtime_context(
        self, db: AsyncSession, run: SkillEvaluationRun, target: AuthorizedEvaluationSkill
    ) -> SkillToolContext:
        """The creator's skill mount and own credential bindings."""
        skill, organization = target.skill, target.organization
        descriptor = skill_service.to_runtime_dict(skill)
        if skill.execution_profile:
            descriptor["execution_profile"] = skill.execution_profile
        cfg = AgentConfig(
            org_context=organization,
            provider="evaluation",
            model_name="evaluation",
            api_key=None,
            base_url=None,
            system_prompt="",
            tools_config=[],
            thread_id=str(run.id),
            agent_skills=[descriptor],
            user_id=str(run.user_id),
            credential_subject_user_id=str(run.user_id),
        )
        data_dir = Path(settings.data_root)
        context = build_skill_runtime_context(
            cfg, data_dir=data_dir, output_root=data_dir / "skill-evaluation-runs"
        )
        context.run_id = str(run.id)
        context.audit_kind = "skill_evaluation"
        try:
            await resolve_runtime_credentials(context, db=db, cfg=cfg)
        except AppError as exc:
            raise SkillEvaluationExecutionError(exc.message) from exc
        return context


async def _load_private_skill(
    db: AsyncSession, run: SkillEvaluationRun
) -> AuthorizedEvaluationSkill:
    scope = (
        await db.execute(
            select(Skill.user_id, Skill.org_id, Skill.tenant_id)
            .where(Skill.id == run.skill_id)
            .execution_options(autoflush=False)
        )
    ).one_or_none()
    if scope is None or scope[0] != run.user_id or scope[1] is None or scope[2] is None:
        raise SkillEvaluationExecutionError(f"skill not found for evaluation: {run.skill_id}")
    try:
        organization = await resolve_private_actor(
            db, PrivateResourceActor(scope[0], scope[1], scope[2])
        )
    except AppError as exc:
        raise SkillEvaluationExecutionError(exc.code) from None
    skill = await db.scalar(
        private_scoped(
            select(Skill).where(Skill.id == run.skill_id), Skill, organization
        ).execution_options(populate_existing=True)
    )
    if skill is None:
        raise SkillEvaluationExecutionError(f"skill not found for evaluation: {run.skill_id}")
    return AuthorizedEvaluationSkill(skill, organization)


__all__ = ["AuthorizedEvaluationSkill", "EVALUATE_PERMISSION", "SkillEvaluationAccessPlanner"]
