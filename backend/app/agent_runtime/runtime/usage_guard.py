"""``UsageLimitGuardMiddleware`` — the in-run spend check of plan 8.7 (H-06).

"실행 시작 전과 모델 호출 직전마다 해당하는 네 한도를 모두 확인합니다 ... 진행 중인 실행이
한도에 닿으면 현재 단계까지 마치고 멈추며, 대화에 '사용 한도에 도달해 멈췄어요'를 남깁니다."

Runtime preparation (every entry: chat, Agent API, triggers, prepared leases, resumes,
and after a restart) asks ``UsageLimits.states`` (10 s cache) for the run's subject —
company, organization, the billed caller (``org_context.user_id``) and the root agent
(``usage_root_agent_id`` on child runtimes, so a subagent spends against its root's
tier) — and installs the guard whenever any tier has a limit. Before every model call it
re-reads the limits in its own company session; at 100% it answers with the stop message
and ends the graph (the step that already ran is kept; in a subagent the parent receives
the stop as the tool result and stops at its own next model call). A failed check, at
preparation or per call, is logged and lets the run through: the start gate already
admitted it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Final
from uuid import UUID

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from app.authz.org_context import OrgContext
from app.database import SessionTenant, async_session, tenant_session
from app.tenancy.limits import Subject, UsageLimits

logger = logging.getLogger(__name__)

GUARD_NAME: Final = "UsageLimitGuardMiddleware"
STOP_MESSAGE: Final = "사용 한도에 도달해 멈췄어요."
PREPARE_TIMEOUT: Final = 2.0


def _session(subject: Subject) -> Any:
    scope = SessionTenant(tenant_id=subject.tenant_id, user_id=subject.user_id)
    return tenant_session(scope, maker=async_session)


class UsageLimitGuardMiddleware(AgentMiddleware):
    def __init__(self, subject: Subject) -> None:
        super().__init__()
        self.subject = subject

    @property
    def name(self) -> str:
        return GUARD_NAME

    async def _blocking_scope(self) -> str | None:
        try:
            async with _session(self.subject) as db:
                blocked = await UsageLimits.blocking(db, self.subject)
        except Exception:  # noqa: BLE001 - never fail a model call on the check itself
            tenant = self.subject.tenant_id
            logger.warning("usage limit check failed tenant=%s", tenant, exc_info=True)
            return None
        return None if blocked is None else blocked.scope

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        scope = await self._blocking_scope()
        if scope is None:
            return None
        stop = AIMessage(
            content=STOP_MESSAGE,
            additional_kwargs={"moldy_stop": {"code": "quota_exceeded", "scope": scope}},
        )
        return {"messages": [stop], "jump_to": "end"}


def subject_of_cfg(cfg: Any) -> Subject | None:
    context = getattr(cfg, "org_context", None)
    if not isinstance(context, OrgContext):
        return None
    agent = getattr(cfg, "usage_root_agent_id", None) or getattr(cfg, "agent_id", None)
    try:
        agent_id = UUID(str(agent)) if agent else None
    except ValueError:
        agent_id = None
    return Subject(context.tenant_id, context.org_id, context.user_id, agent_id)


async def _limited(subject: Subject) -> bool:
    async with _session(subject) as db:
        return bool(await UsageLimits.states(db, subject))


async def with_usage_guard(middleware: list[Any], cfg: Any) -> list[Any]:
    """``middleware`` plus the guard when any tier of the run's subject has a limit."""
    subject = subject_of_cfg(cfg)
    if subject is None:
        return middleware
    try:
        limited = await asyncio.wait_for(_limited(subject), PREPARE_TIMEOUT)
    except Exception:  # noqa: BLE001 - preparation never fails on the limit lookup
        logger.warning("usage limit lookup failed tenant=%s", subject.tenant_id, exc_info=True)
        return middleware
    return [*middleware, UsageLimitGuardMiddleware(subject)] if limited else middleware


__all__ = [
    "GUARD_NAME",
    "STOP_MESSAGE",
    "UsageLimitGuardMiddleware",
    "subject_of_cfg",
    "with_usage_guard",
]
