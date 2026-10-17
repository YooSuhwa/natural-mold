"""Request rate limiter (slowapi) and its company-scoped keys (I-08, plan 8.9).

Buckets never mix companies:

* authenticated routes key on ``(tenant_id, user_id)`` from the resolved org
  context (``caller_key``); a caller without an org context — platform operator
  routes — gets a separate ``platform`` bucket;
* public share routes key on the token's company plus the client IP
  (``share_key``; the company is looked up by ``public_share_rate_scope``
  before the check, an unknown token falls into an ``unknown`` bucket);
* pre-auth routes (login, register, SSO) keep the plain per-IP default key.

``get_remote_address`` reads ``request.client.host``. Tests flip
``settings.rate_limit_enabled`` off via the conftest so route behavior
assertions stay deterministic.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.authz.org_context import OrgContext
from app.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    enabled=settings.rate_limit_enabled,
)


def caller_key(scope: str) -> Callable[[Request], str]:
    """Key for authenticated routes: ``(tenant_id, user_id)``, or the platform bucket."""

    def key(request: Request) -> str:
        context: object = getattr(request.state, "org_context", None)
        if isinstance(context, OrgContext):
            return f"{scope}:tenant:{context.tenant_id}:user:{context.user_id}"
        actor_id: object = getattr(getattr(request.state, "current_user", None), "id", None)
        if isinstance(actor_id, UUID):
            return f"{scope}:platform:user:{actor_id}"
        return f"{scope}:unauthenticated:{get_remote_address(request)}"

    return key


def share_key(request: Request) -> str:
    """Public share bucket: the token's company plus the client IP."""

    tenant: object = getattr(request.state, "share_tenant_id", None)
    company = str(tenant) if isinstance(tenant, UUID) else "unknown"
    return f"share:tenant:{company}:ip:{get_remote_address(request)}"
