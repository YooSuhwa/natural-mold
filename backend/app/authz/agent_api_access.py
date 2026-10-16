"""Agent API key-holder decisions on top of the private gates (plan 9.2, 11.6; D-06).

Deployments, API keys and API threads stay PRIVATE (owner AND current org): the
existing owner-scoped loaders are the legacy answer. On top of them every runtime
request (``KEY`` policy) re-checks the key owner's ``agent#can_deploy`` on the
deployment's agent, so a key whose owner lost the permission is refused at once
while the key itself stays valid (the opaque 401 contract only covers key state).
Deployment create/update and key create check the same permission.

``AUTHZ_MODE=legacy`` returns before any sample or query (byte-identical);
``shadow`` records the legacy answer; ``enforce`` asks OpenFGA. Management routes
split 8.4-style (no ``can_run`` → the caller's existing 404, ``can_run`` without
``can_deploy`` → 403 ``permission_required``); runtime (``/v1``) call sites pass
``opaque=True`` so every denial is the caller's existing 404. OpenFGA down → 503.

Routes bind their ``get_authz`` service (overrides, shadow request stamp) and a
per-request memo: one decision (and shadow sample) per (principal, agent) per
request. ``fresh_decision_scope`` opens a new memo for the prepared-lease resolver,
whose ``fresh=True`` re-check runs before the materialization locks so the locked
re-reads reuse it. Detached work falls back to ``runtime_authz()``.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

from fastapi import Depends

from app.authz import runtime
from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.service import AuthzService, Verdict
from app.exceptions import AppError

DEPLOY = "can_deploy"
MIN_VISIBLE = "can_run"
_bound: ContextVar[AuthzService | None] = ContextVar("agent_api_authz", default=None)
_Key = tuple[UUID, UUID | None, UUID | None, UUID]
_allowed: ContextVar[set[_Key] | None] = ContextVar("agent_api_deploy_memo", default=None)


async def bind_agent_api_authz(authz: AuthzService = Depends(runtime.get_authz)) -> AuthzService:
    """Route dependency: decisions made deep in the runtime services use this service."""
    _bound.set(authz)
    _allowed.set(set())
    return authz


def _service(authz: AuthzService | None) -> AuthzService:
    return authz or _bound.get() or runtime.runtime_authz()


async def require_can_deploy(
    context: OrgContext,
    agent_id: UUID,
    *,
    not_found: AppError,
    opaque: bool = False,
    fresh: bool = False,
    authz: AuthzService | None = None,
) -> None:
    """The legacy private gate already accepted ``agent_id`` in ``context``'s org."""
    service = _service(authz)
    if service.mode == "legacy":
        return
    memo = _allowed.get()
    key = (context.user_id, context.org_id, context.tenant_id, agent_id)
    if memo is not None and key in memo and not fresh:
        return
    await service.require(
        Principal.for_user(context),
        DEPLOY,
        f"agent:{agent_id}",
        min_visible=DEPLOY if opaque else MIN_VISIBLE,
        resource_org_id=context.org_id,
        legacy=Verdict.ALLOW,
        not_found=not_found,
        request_access=False,
    )
    if memo is not None:
        memo.add(key)


@contextmanager
def fresh_decision_scope() -> Iterator[None]:
    """A new memo, so a re-check is a separate decision even inside a request."""
    token = _allowed.set(set())
    try:
        yield
    finally:
        _allowed.reset(token)


async def deployable_agent_ids(
    context: OrgContext, agent_ids: Sequence[UUID], *, authz: AuthzService | None = None
) -> set[UUID]:
    """The subset of privately visible agents the caller may (still) deploy."""
    service = _service(authz)
    if service.mode == "legacy" or not agent_ids:
        return set(agent_ids)
    objs = [f"agent:{agent_id}" for agent_id in agent_ids]
    kept = set(await service.filter_visible(Principal.for_user(context), DEPLOY, objs, legacy=objs))
    return {agent_id for agent_id in agent_ids if f"agent:{agent_id}" in kept}
