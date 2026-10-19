"""Runtime handling of typed MCP auth errors (plan 11.4, 11.4.1, 11.5; F-12).

``McpAuthContext`` is built from one MCP tool config (``build_tools_config``) and
holds no secret: only ids, the server's display name, who can fix the
connection and the previously granted OAuth scope. ``auth_error_result`` turns
a ``McpAuthError`` into the error ToolMessage payload every surface reads the
same way: model-visible text plus an ``mcp_auth_error`` artifact that carries
the 11.5 ``auth_required`` interrupt payload (``ConnectionGuardMiddleware``,
F-07/G-03, lifts it into the actual interrupt; trigger runs end ``blocked``).

* ``auth_required`` (401) marks the credential ``auth_needed`` (and evicts its
  cached MCP tools) only when the run actor owns it and the server said
  ``invalid_token``; otherwise the typed error is returned without a write.
* ``insufficient_scope`` asks for ``reason=scope_upgrade`` with the merged scope
  (F-15 ``plan_scope_step_up``); the same server and tool step up at most
  ``MAX_STEP_UP_ATTEMPTS`` times per conversation (the budget survives the
  runtime rebuild that a ``retry`` resume does; trigger runs: per run), then the
  call just fails. The budget is per process (another worker starts at zero);
  merged scopes over ``MAX_SCOPE_CHARS``/``MAX_SCOPES`` are refused.
* ``permission_denied`` (other 403) is a permission error: no interrupt.
"""

from __future__ import annotations

import logging
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

from sqlalchemy import update

from app.agent_runtime.mcp_auth_errors import McpAuthError
from app.credentials.mcp_oauth_step_up import ScopeStepUpBudget, merge_scopes, plan_scope_step_up
from app.database import SessionTenant, async_session, tenant_session
from app.models.credential import Credential

logger = logging.getLogger(__name__)
ARTIFACT_KEY = "mcp_auth_error"
_BUDGET_LIMIT = 1024
MAX_SCOPE_CHARS = 1024
MAX_SCOPES = 64
_BUDGETS: OrderedDict[str, ScopeStepUpBudget] = OrderedDict()
_OAUTH_FIELDS = ("access_token", "refresh_token", "client_id")


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class McpAuthContext:
    server_url: str
    server_name: str
    server_id: str | None
    credential_id: str | None
    credential_mode: str
    fixable_by: Literal["me", "owner"]
    connect_kind: Literal["oauth", "api_key", "binding"]
    granted_scope: str | None
    conversation_id: str | None
    run_source: str | None
    tenant_id: str | None

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> McpAuthContext | None:
        url = str(config.get("mcp_server_url") or "")
        if not url:
            return None
        values = config.get("credentials")
        values = values if isinstance(values, dict) else {}
        mode = str(config.get("credential_mode") or "shared")
        credential_id = config.get("credential_id")
        if credential_id is None and mode == "per_user":
            kind: Literal["oauth", "api_key", "binding"] = "binding"
        elif any(values.get(field) for field in _OAUTH_FIELDS):
            kind = "oauth"
        else:
            kind = "api_key"
        scope = values.get("scope")
        fixable = "owner" if config.get("credential_fixable_by") == "owner" else "me"
        return cls(
            server_url=url,
            server_name=str(config.get("mcp_server_name") or urlparse(url).hostname or url),
            server_id=str(config["mcp_server_id"]) if config.get("mcp_server_id") else None,
            credential_id=str(credential_id) if credential_id else None,
            credential_mode=mode,
            fixable_by=fixable,
            connect_kind=kind,
            granted_scope=str(scope) if scope else None,
            conversation_id=str(config["conversation_id"])
            if config.get("conversation_id")
            else None,
            run_source=str(config["run_source"]) if config.get("run_source") else None,
            tenant_id=str(config["tenant_id"]) if config.get("tenant_id") else None,
        )


def mcp_auth_fields(
    server: Any, resolved: Any, actor_user_id: object, identity: Any = None
) -> dict[str, Any]:
    """Tool-config fields ``build_tools_config`` adds to an MCP entry (no secrets)."""

    source = getattr(identity, "source", None)
    owner = resolved.credential_subject_id if resolved is not None else server.user_id
    return {
        "mcp_server_name": server.name,
        "credential_id": str(resolved.credential_id) if resolved is not None else None,
        "credential_mode": "per_user" if server.credential_mode == "per_user" else "shared",
        "credential_fixable_by": "me" if str(owner) == str(actor_user_id) else "owner",
        "run_source": None if source is None else str(getattr(source, "value", source)),
    }


def budget_scope(context: McpAuthContext, run_id: str | None) -> str:
    """Conversation runs share the cap across ``retry`` resumes; trigger runs
    (and runs without a conversation) are capped per run, never agent-wide."""

    if context.conversation_id and context.run_source != "trigger":
        return f"conversation:{context.conversation_id}"
    if run_id:
        return f"run:{run_id}"
    return f"call:{uuid.uuid4()}"  # no run id: this call alone


def _scope_too_large(scope: str) -> bool:
    return len(scope) > MAX_SCOPE_CHARS or len(scope.split()) > MAX_SCOPES


def step_up_budget(scope: str) -> ScopeStepUpBudget:
    budget = _BUDGETS.get(scope)
    if budget is None:
        budget = _BUDGETS[scope] = ScopeStepUpBudget()
        while len(_BUDGETS) > _BUDGET_LIMIT:
            _BUDGETS.popitem(last=False)
    _BUDGETS.move_to_end(scope)
    return budget


def _interrupt(error: McpAuthError, context: McpAuthContext, tool_name: str) -> dict[str, Any]:
    item = {
        "resource_type": "mcp_server",
        "resource_name": context.server_name,
        "tool_names": [tool_name],
        "credential_id": context.credential_id,
        "fixable_by": context.fixable_by,
        "connect": {"kind": context.connect_kind},
    }
    return {
        "type": "auth_required",
        "reason": error.reason,
        "items": [item],
        "allowed_decisions": ["respond"],
    }


def _step_up(
    error: McpAuthError, context: McpAuthContext, tool_name: str, run_id: str | None
) -> dict | None:
    challenge = error.challenge
    if challenge is None:
        return None
    if _scope_too_large(merge_scopes(context.granted_scope, challenge.scope)):
        return None  # an oversized challenged scope is refused, never stored or relayed
    planned = plan_scope_step_up(
        credential_data={"scope": context.granted_scope},
        challenge=challenge,
        budget=step_up_budget(budget_scope(context, run_id)),
        resource=context.server_url,
        resource_name=context.server_name,
        tool_name=tool_name,
        credential_id=_uuid(context.credential_id),
        fixable_by=context.fixable_by,
    )
    return None if planned is None else planned.interrupt


_MESSAGES = {
    "auth_required": "the MCP connection needs to be authorized again",
    "insufficient_scope": "the MCP connection needs additional permission (scope)",
    "permission_denied": "the MCP server denied permission for this call",
}


def _should_mark(error: McpAuthError, context: McpAuthContext) -> bool:
    """Only the actor's own credential, bound to this server, on ``invalid_token``.

    A shared credential owned by someone else is never flipped by another
    member's run (the owner is asked instead: ``fixable_by=owner``).
    """

    return (
        context.fixable_by == "me"
        and context.credential_id is not None
        and error.error_code == "invalid_token"
    )


async def auth_error_result(
    error: McpAuthError,
    context: McpAuthContext | None,
    tool_name: str,
    *,
    run_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Model-visible error text and the ``mcp_auth_error`` artifact."""

    interrupt: dict[str, Any] | None = None
    exhausted = False
    if context is not None and error.kind == "auth_required":
        interrupt = _interrupt(error, context, tool_name)
        if _should_mark(error, context):
            await mark_credential_auth_needed(context)
    elif context is not None and error.kind == "insufficient_scope":
        interrupt = _step_up(error, context, tool_name, run_id)
        exhausted = interrupt is None
    detail = _MESSAGES[error.kind] + (" (step-up limit reached)" if exhausted else "")
    payload: dict[str, Any] = {
        "kind": error.kind,
        "status_code": error.status_code,
        "reason": error.reason if error.kind != "permission_denied" else None,
        "server_id": context.server_id if context else None,
        "credential_id": context.credential_id if context else None,
        "step_up_exhausted": exhausted,
        "interrupt": interrupt,
    }
    content = f"[MCP Auth Error] {tool_name}: {error.kind} (HTTP {error.status_code}): {detail}"
    return content, {ARTIFACT_KEY: payload}


async def mark_credential_auth_needed(context: McpAuthContext) -> None:
    """``active`` → ``auth_needed`` on the run's credential (own session; best effort)."""

    credential_id, tenant_id = _uuid(context.credential_id), _uuid(context.tenant_id)
    if credential_id is None or tenant_id is None:
        return
    from app.agent_runtime.mcp_tool_cache import invalidate_mcp_tool_cache

    invalidate_mcp_tool_cache(credential_id=str(credential_id))
    scope = SessionTenant(tenant_id)  # the credential may be another member's (shared)
    try:
        async with tenant_session(scope, maker=async_session) as db:
            await db.execute(
                update(Credential)
                .where(Credential.id == credential_id, Credential.status == "active")
                .values(status="auth_needed")
            )
            await db.commit()
    except Exception:  # noqa: BLE001 - the error ToolMessage still reaches the run
        logger.warning("could not mark MCP credential %s auth_needed", credential_id)


__all__ = [
    "ARTIFACT_KEY",
    "McpAuthContext",
    "auth_error_result",
    "mark_credential_auth_needed",
    "mcp_auth_fields",
    "budget_scope",
    "step_up_budget",
]
