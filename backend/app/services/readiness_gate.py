"""Chat start/send/resume refuse a ``blocked`` agent before any row (plan 12.2, 12.3; G-02).

"대화 시작·전송·재개 API는 같은 계산을 내부에서 호출해 막히면 409 agent_not_ready 로 같은
본문을 돌려줍니다": ``require_agent_ready`` runs the readiness check (``assess_agent``, the
F-06 decisions without raising, through the request's decision memo) and raises
``agent_not_ready`` with the 12.3 body (``present``: per-agent ``can_view_config``
visibility) when the overall state is ``blocked``. ``needs_connection`` and ``limited``
start: the connection guard asks in-run (F-07) and optional links are left out (F-06).

While no run type is enforced (``legacy``, ``shadow``) nothing is assessed, so legacy
requests keep their queries and answers. Never cached: the summary cache is advisory.

A resume whose every answer ends or abandons the run (``abandons_run``: an
``auth_required`` ``cancel`` (F-07), or HITL decisions that are all ``reject``) skips the
gate, so a user can always get out of a pending interrupt; other resumes keep the 409.
"""

from collections.abc import Iterable, Mapping
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime.connection_guard_items import decision_of
from app.authz.conversation_agent_checks import current_authz
from app.authz.error_codes import agent_not_ready
from app.authz.org_context import OrgContext
from app.authz.runtime_access_planner import runtime_access_enforced
from app.authz.service import AuthzService


async def require_agent_ready(
    db: AsyncSession,
    context: OrgContext,
    agent_id: UUID,
    *,
    authz: AuthzService | None = None,
) -> None:
    service = authz or current_authz()
    if not runtime_access_enforced(service):
        return
    from app.services import readiness_service  # call time: chat run prep imports this module

    readiness = await readiness_service.assess_agent(db, service, context, agent_id)
    if readiness.overall == "blocked":
        body = readiness_service.present(readiness)
        raise agent_not_ready(
            body["items"], identity_mode=body["identity_mode"], agent_id=body.get("agent_id")
        )


def resume_answers(resume: object) -> list[object]:
    """The submitted answers of a REST ``ResumeRequest`` or a protocol resume payload."""
    if isinstance(resume, BaseModel):
        return [resume.model_dump(exclude_none=True)]
    submitted = getattr(resume, "submitted", None)
    if submitted:
        return [entry.response for entry in submitted]
    return [getattr(resume, "input_payload", resume)]


def abandons_run(answers: Iterable[object]) -> bool:
    answers = list(answers)
    return bool(answers) and all(_abandons(answer) for answer in answers)


def _abandons(answer: object) -> bool:
    if decision_of(answer) == "cancel":  # ends the run (``jump_to=end``)
        return True
    decisions = answer.get("decisions") if isinstance(answer, Mapping) else None
    return (
        isinstance(decisions, list)
        and bool(decisions)
        and all(isinstance(item, Mapping) and item.get("type") == "reject" for item in decisions)
    )


__all__ = ["abandons_run", "require_agent_ready", "resume_answers"]
