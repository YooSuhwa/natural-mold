"""Registration review: an administrator checks what a member registered (plan 3.7).

When a company turns ``review`` on, a tool or an MCP server registered by someone without
the kind's capability starts ``pending``: its registrant may edit it and test the
connection, but nobody attaches it to an agent and no run loads it. The request sits in
the approval inbox as ``kind="registration"`` and is decided by company administrators and
the administrators of the resource's organization, not by one named person.

The card is a snapshot of what the administrator is asked to trust: where the server
lives, how it authenticates (names only, never values) and the tools it serves. A decision
is taken against the live resource: when the resource changed since the card was made, the
card is refreshed and the administrator is asked to look again rather than approve
something unseen. Approving a server also approves the tool versions on the card, and
stores the server's connection fingerprint; a later change of address, command, transport
or authentication by someone under review sends it back to ``pending``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Final, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import service
from app.approvals.recording import INBOX_LINK
from app.authz import audit_actions, registration, registration_state
from app.authz.notifications import Target, notify
from app.authz.org_administrators import active_administrators
from app.authz.org_context import OrgContext
from app.mcp import review
from app.models.approval_request import ApprovalRequest
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.organization import Organization, TenantRoleGrant
from app.models.tool import Tool
from app.models.user import User
from app.services import audit_service
from app.tools.risk import mcp_tool_risk

KIND: Final = "registration"
ACTION_NAME: Final = "registration_review"
DECISIONS: Final = ("approve", "reject")
EXPIRES: Final = timedelta(days=30)
MAX_TOOLS: Final = 200
DESCRIPTION_LIMIT: Final = 500
Reason = Literal["new", "changed", "tools"]
Resource = McpServer | Tool


class RegistrationStale(Exception):
    """The resource changed since the card was made; the card now shows the new state."""


def resource_type(resource: Resource) -> str:
    return "mcp_server" if isinstance(resource, McpServer) else "tool"


fingerprint = registration_state.fingerprint
usable = registration_state.registered


def owner_context(resource: Resource) -> OrgContext | None:
    if resource.user_id is None or resource.org_id is None or resource.tenant_id is None:
        return None  # a system row: registered by the platform, not by a member
    return OrgContext(
        org_id=resource.org_id, tenant_id=resource.tenant_id, user_id=resource.user_id
    )


# --- the card ----------------------------------------------------------------------


async def _credential_name(db: AsyncSession, credential_id: uuid.UUID | None) -> str | None:
    if credential_id is None:
        return None
    return await db.scalar(select(Credential.name).where(Credential.id == credential_id))


async def _registrant(db: AsyncSession, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    row = (await db.execute(select(User.name, User.email).where(User.id == user_id))).one_or_none()
    return (row[0] or row[1]) if row else None


def _tool_entry(tool: McpTool, server: McpServer, *, every: bool) -> dict[str, Any] | None:
    """A tool as the administrator sees it: a version to review, or one already approved."""
    pending = review.pending_view(tool, server.url)
    if pending is None:
        if not every or tool.review_state != "approved":
            return None
        shown: dict[str, Any] = {**review.Definition.from_row(tool).as_json(), "fingerprint": None}
        change = "approved"
    else:
        shown = pending
        change = "changed" if tool.review_state == "approved" else "new"
    annotations = shown.get("annotations")
    risk = mcp_tool_risk(tool.name, metadata=annotations if isinstance(annotations, dict) else {})
    return {
        "id": str(tool.id),
        "name": tool.name,
        # Text from outside: stored as plain characters and shown as such.
        "description": str(shown.get("description") or "")[:DESCRIPTION_LIMIT],
        "read_only": str(getattr(risk.risk_level, "value", risk.risk_level)) == "read_only",
        "fingerprint": shown.get("fingerprint"),
        "change": change,
    }


async def _server_tools(
    db: AsyncSession, server: McpServer, reason: Reason
) -> list[dict[str, Any]]:
    """A registration shows every tool; a tool change shows the versions to review."""
    rows = (
        await db.scalars(
            select(McpTool).where(McpTool.server_id == server.id).order_by(McpTool.name)
        )
    ).all()
    entries = [
        entry
        for tool in rows
        if (entry := _tool_entry(tool, server, every=reason != "tools")) is not None
    ]
    return entries[:MAX_TOOLS]


async def card(db: AsyncSession, resource: Resource, reason: Reason) -> list[dict[str, Any]]:
    """The request's ``actions``: one entry holding what is to be trusted."""
    args: dict[str, Any] = {
        "title": resource.name,
        "resource_type": resource_type(resource),
        "resource_id": str(resource.id),
        "reason": reason,
        "registrant": await _registrant(db, resource.user_id),
        "credential": await _credential_name(db, resource.credential_id),
        "credential_mode": resource.credential_mode,
    }
    if isinstance(resource, McpServer):
        args.update(
            transport=resource.transport,
            url=resource.url,
            command=resource.command,
            args=[str(item) for item in resource.args or []],
            header_keys=sorted((resource.headers or {}).keys()),
            env_keys=sorted((resource.env_vars or {}).keys()),
            fingerprint=fingerprint(resource),
            tools=await _server_tools(db, resource, reason),
        )
    else:
        args.update(definition_key=resource.definition_key, description=resource.description)
    return [
        {
            "name": ACTION_NAME,
            "args": args,
            "allowed_decisions": list(DECISIONS),
            "description": resource.name,
        }
    ]


def _reason(row: ApprovalRequest) -> Reason:
    value = (row.actions[0].get("args") or {}).get("reason") if row.actions else None
    return value if value in ("new", "changed", "tools") else "new"


# --- who decides -------------------------------------------------------------------


async def deciders(db: AsyncSession, row: ApprovalRequest) -> set[uuid.UUID]:
    """Company administrators and the administrators of the resource's organization."""
    found = set(
        (
            await db.scalars(
                select(TenantRoleGrant.user_id).where(
                    TenantRoleGrant.tenant_id == row.tenant_id, TenantRoleGrant.role == "admin"
                )
            )
        ).all()
    )
    organization = await db.get(Organization, row.org_id) if row.org_id else None
    if organization is not None:
        found |= await active_administrators(db, organization)
    return found


async def can_decide(db: AsyncSession, row: ApprovalRequest, context: OrgContext) -> bool:
    if row.tenant_id != context.tenant_id:
        return False
    scope = await registration.decider_scope(db, context)
    return scope == "company" or (scope == "organization" and row.org_id == context.org_id)


# --- the request -------------------------------------------------------------------


async def load_resource(db: AsyncSession, row: ApprovalRequest) -> Resource | None:
    if row.resource_id is None:
        return None
    model = McpServer if row.resource_type == "mcp_server" else Tool
    return await db.get(model, row.resource_id)


async def pending_request(db: AsyncSession, resource: Resource) -> ApprovalRequest | None:
    return await db.scalar(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.kind == KIND,
            ApprovalRequest.resource_type == resource_type(resource),
            ApprovalRequest.resource_id == resource.id,
            ApprovalRequest.status == "pending",
        )
        .order_by(ApprovalRequest.created_at.desc())
        .limit(1)
    )


async def last_request(db: AsyncSession, resource: Resource) -> ApprovalRequest | None:
    return await db.scalar(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.kind == KIND,
            ApprovalRequest.resource_type == resource_type(resource),
            ApprovalRequest.resource_id == resource.id,
        )
        # The request that waits is the one to show, whatever the clock says of two rows.
        .order_by(
            (ApprovalRequest.status == "pending").desc(),
            ApprovalRequest.created_at.desc(),
            ApprovalRequest.id.desc(),
        )
        .limit(1)
    )


async def _audit(
    db: AsyncSession,
    resource: Resource,
    action: str,
    *,
    actor_id: uuid.UUID | None,
    row: ApprovalRequest | None,
    outcome: str = "success",
    reason_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    await audit_service.record_event(
        db,
        actor_type="user" if actor_id else "system",
        actor_user_id=actor_id,
        action=action,
        target_type=resource_type(resource),
        target_id=resource.id,
        target_name_snapshot=resource.name,
        target_owner_user_id=resource.user_id,
        owner_user_id=resource.user_id,
        outcome=outcome,
        reason_message=reason_message,
        tenant_id=resource.tenant_id,
        org_id=resource.org_id,
        metadata={
            "approval_request_id": str(row.id) if row else None,
            "review_reason": _reason(row) if row else None,
            "registration_state": resource.registration_state,
            **(metadata or {}),
        },
    )


async def cancel_pending(db: AsyncSession, resource: Resource, reason: str) -> None:
    await db.execute(
        update(ApprovalRequest)
        .where(
            ApprovalRequest.kind == KIND,
            ApprovalRequest.resource_type == resource_type(resource),
            ApprovalRequest.resource_id == resource.id,
            ApprovalRequest.status == "pending",
        )
        .values(status="canceled", reason=reason, decided_at=service.now())
        .execution_options(synchronize_session=False)
    )


async def submit(
    db: AsyncSession,
    resource: Resource,
    *,
    reason: Reason,
    actor_id: uuid.UUID | None,
) -> ApprovalRequest | None:
    """Ask the administrators; one pending request per resource (an older one is retired)."""
    context = owner_context(resource)
    if context is None:
        return None
    await db.flush()
    await cancel_pending(db, resource, "resource_changed")
    actions = await card(db, resource, reason)
    row = ApprovalRequest(
        tenant_id=context.tenant_id,
        org_id=context.org_id,
        conversation_id=None,
        run_id=None,
        interrupt_id=f"registration:{resource_type(resource)}:{resource.id}:{uuid.uuid4().hex}",
        namespace=[],
        kind=KIND,
        resource_type=resource_type(resource),
        resource_id=resource.id,
        agent_id=None,
        agent_name=resource.name[:200],
        source="registration",
        subject_user_id=context.user_id,
        requester_user_id=context.user_id,
        # Administrators decide it (``deciders``); the column only keeps the row's shape.
        decider_user_id=context.user_id,
        actions=actions,
        args_digest=service.digest(actions),
        expires_at=service.now() + EXPIRES,
    )
    db.add(row)
    await db.flush()
    await _audit(db, resource, audit_actions.REGISTRATION_REQUESTED, actor_id=actor_id, row=row)
    await notify(
        db,
        kind="approval.requested",
        recipients=await deciders(db, row),
        tenant_id=row.tenant_id,
        org_id=row.org_id,
        target=Target("approval_request", row.id, row.agent_name),
        actor_id=actor_id,
        payload={"source": row.source, "action_count": 1},
        link=INBOX_LINK,
    )
    return row


async def hold(db: AsyncSession, resource: Resource, *, actor_id: uuid.UUID | None) -> None:
    """A new registration under review: ``pending`` until an administrator decides."""
    resource.registration_state = "pending"
    await submit(db, resource, reason="new", actor_id=actor_id)


async def refresh(db: AsyncSession, row: ApprovalRequest, resource: Resource) -> bool:
    """Bring a pending card up to the live resource; ``True`` when it changed."""
    actions = await card(db, resource, _reason(row))
    digest = service.digest(actions)
    if digest == row.args_digest:
        return False
    row.actions = actions
    row.args_digest = digest
    row.agent_name = resource.name[:200]
    await db.flush()
    return True


# --- changes after the registration ------------------------------------------------


async def recheck(db: AsyncSession, context: OrgContext, server: McpServer, *, before: str) -> None:
    """After an edit: a changed connection by someone under review waits again (3.7)."""
    after = fingerprint(server)
    if after == before:
        row = await pending_request(db, server)
        if row is not None:  # the name or description on a waiting card
            await refresh(db, row, server)
        return
    if await registration.review_needed(db, context, "mcp_server"):
        was = server.registration_state
        server.registration_state = "pending"
        row = await submit(
            db, server, reason="new" if was != "approved" else "changed", actor_id=context.user_id
        )
        if was == "approved":
            await _audit(
                db,
                server,
                audit_actions.REGISTRATION_RESET,
                actor_id=context.user_id,
                row=row,
                metadata={"fingerprint_before": before, "fingerprint_after": after},
            )
        return
    if server.registration_fingerprint is not None:
        # Changed by someone who registers for the company: the change is the approval.
        server.registration_fingerprint = after
    row = await pending_request(db, server)
    if row is not None:
        await refresh(db, row, server)


async def tools_changed(db: AsyncSession, server: McpServer) -> bool:
    """Discovery found tool versions to review. ``True``: administrators were asked.

    ``False`` leaves it to the server's managers (the ordinary review, D11).
    """
    context = owner_context(server)
    if context is None:
        return False
    row = await pending_request(db, server)
    if row is not None:
        await refresh(db, row, server)
        return True
    if not await registration.review_needed(db, context, "mcp_server"):
        return False
    if usable(server):  # otherwise the registrant asks again and that card shows them
        await submit(db, server, reason="tools", actor_id=None)
    return True


async def reviewed_by_administrators(db: AsyncSession, server: McpServer) -> bool:
    """Whether this server's tool versions are the administrators' to review (G10)."""
    if not usable(server):
        return True
    context = owner_context(server)
    return context is not None and await registration.review_needed(db, context, "mcp_server")


async def request_again(
    db: AsyncSession, context: OrgContext, resource: Resource
) -> ApprovalRequest | None:
    """Ask again after a rejection, an expiry or a fix. ``None``: nothing waits any more.

    Whether it waits follows the registrant, not whoever presses the button: an editor
    the server was shared with does not approve it by asking. An administrator who may
    decide the request asking is the decision.
    """
    owner = owner_context(resource)
    if owner is None or usable(resource):
        return None
    scope = await registration.decider_scope(db, context)
    decides = scope == "company" or (scope == "organization" and owner.org_id == context.org_id)
    if decides or not await registration.review_needed(db, owner, resource_type(resource)):
        # An administrator, or review is off now, or the registrant registers for the company.
        await cancel_pending(db, resource, "no_longer_needed")
        await _approve(db, resource, actor_id=context.user_id, row=None)
        return None
    row = await pending_request(db, resource)
    if row is not None:  # already asked: the card follows the resource, nobody is told twice
        await refresh(db, row, resource)
        return row
    resource.registration_state = "pending"
    return await submit(db, resource, reason="new", actor_id=context.user_id)


async def status(db: AsyncSession, resource: Resource) -> dict[str, Any]:
    """What the registrant sees: the state, the last answer and whether to ask again."""
    state = registration_state.effective_state(resource)
    row = await last_request(db, resource)
    waiting = row is not None and row.status == "pending"
    return {
        "state": state,
        "can_request": state != "approved" and not waiting,
        "administrators_review_tools": isinstance(resource, McpServer)
        and await reviewed_by_administrators(db, resource),
        "request": None
        if row is None
        else {
            "id": row.id,
            "status": row.status,
            "reason": row.reason,
            "requested_at": row.created_at,
            "decided_at": row.decided_at,
        },
    }


# --- the decision ------------------------------------------------------------------


def _card_tools(row: ApprovalRequest) -> list[dict[str, Any]]:
    tools = (row.actions[0].get("args") or {}).get("tools") if row.actions else None
    return [t for t in tools or [] if isinstance(t, dict)]


async def _decide_tools(
    db: AsyncSession,
    server: McpServer,
    row: ApprovalRequest,
    decision: review.Decision,
    actor_id: uuid.UUID,
) -> int:
    shown = {
        str(t.get("id")): t.get("fingerprint") for t in _card_tools(row) if t.get("fingerprint")
    }
    if not shown:
        return 0
    tools = (await db.scalars(select(McpTool).where(McpTool.server_id == server.id))).all()
    done = 0
    for tool in tools:
        if str(tool.id) not in shown:
            continue
        try:
            review.decide(
                tool,
                decision,
                reviewer_id=actor_id,
                server_url=server.url,
                expected_fingerprint=shown[str(tool.id)],
            )
        except review.ReviewConflict as exc:  # the digest check makes this unreachable
            raise RegistrationStale(str(exc)) from exc
        done += 1
    return done


async def _approve(
    db: AsyncSession, resource: Resource, *, actor_id: uuid.UUID | None, row: ApprovalRequest | None
) -> None:
    resource.registration_state = "approved"
    if isinstance(resource, McpServer):
        resource.registration_fingerprint = fingerprint(resource)
    await _audit(db, resource, audit_actions.REGISTRATION_APPROVED, actor_id=actor_id, row=row)


async def decide(
    db: AsyncSession,
    row: ApprovalRequest,
    *,
    decision: str,
    message: str | None,
    actor_id: uuid.UUID,
) -> ApprovalRequest:
    """An administrator's answer. Raises ``RegistrationStale`` or ``service.ApprovalStale``."""
    resource = await load_resource(db, row)
    if resource is None:
        await cancel_pending_row(db, row, "resource_deleted")
        raise RegistrationStale("the registered resource no longer exists")
    if await refresh(db, row, resource):
        raise RegistrationStale("the registration changed since the request was made")
    reason = _reason(row)
    await service.claim_business(db, row, decision=decision, channel="inbox", actor_id=actor_id)
    note = (message or "").strip()[:200] or None
    row.reason = note
    tools = 0
    if isinstance(resource, McpServer):
        tools = await _decide_tools(
            db, resource, row, "approve" if decision == "approve" else "reject", actor_id
        )
    if decision == "approve" and reason == "tools":
        # Only the tool versions were asked about: the server stays as it was approved.
        await _audit(
            db,
            resource,
            audit_actions.REGISTRATION_APPROVED,
            actor_id=actor_id,
            row=row,
            metadata={"tools": tools},
        )
    elif decision == "approve":
        await _approve(db, resource, actor_id=actor_id, row=row)
    else:
        if reason != "tools":  # a rejected tool change leaves the approved server as it is
            resource.registration_state = "rejected"
        await _audit(
            db,
            resource,
            audit_actions.REGISTRATION_REJECTED,
            actor_id=actor_id,
            row=row,
            outcome="denied",
            reason_message=note,
            metadata={"tools": tools},
        )
    await db.flush()
    await _notify_registrant(db, row, actor_id=actor_id)
    return row


async def cancel_pending_row(db: AsyncSession, row: ApprovalRequest, reason: str) -> None:
    await db.execute(
        update(ApprovalRequest)
        .where(ApprovalRequest.id == row.id, ApprovalRequest.status == "pending")
        .values(status="canceled", reason=reason, decided_at=service.now())
        .execution_options(synchronize_session=False)
    )


async def _notify_registrant(
    db: AsyncSession, row: ApprovalRequest, *, actor_id: uuid.UUID | None
) -> None:
    await notify(
        db,
        kind="approval.decided",
        recipients=[row.subject_user_id],
        tenant_id=row.tenant_id,
        org_id=row.org_id,
        target=Target("approval_request", row.id, row.agent_name),
        actor_id=actor_id,
        payload={"status": row.status, "source": row.source, "reason": row.reason},
        link=_resource_link(row),
    )


def _resource_link(row: ApprovalRequest) -> str:
    return "/mcp-servers" if row.resource_type == "mcp_server" else "/tools"


async def expire(db: AsyncSession, row: ApprovalRequest) -> None:
    """Nobody decided in time: the resource stays as it is and the registrant may ask again."""
    try:
        await service.claim_business(db, row, decision="reject", channel="expiry", actor_id=None)
    except service.ApprovalStale:
        return
    await _notify_registrant(db, row, actor_id=None)


__all__ = [
    "KIND",
    "RegistrationStale",
    "can_decide",
    "cancel_pending",
    "card",
    "decide",
    "deciders",
    "expire",
    "fingerprint",
    "hold",
    "last_request",
    "load_resource",
    "owner_context",
    "pending_request",
    "recheck",
    "refresh",
    "request_again",
    "resource_type",
    "reviewed_by_administrators",
    "status",
    "submit",
    "tools_changed",
    "usable",
]
