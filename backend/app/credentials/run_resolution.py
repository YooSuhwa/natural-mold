"""Run-time credential resolution for tools and MCP servers (plan 11.1, 11.3; F-05).

* ``shared`` (and ``none``/legacy rows carrying a ``credential_id``): the resource's own
  credential, which must belong to the resource owner; the 11.1 decision subject needs
  resource ``can_use`` (skipped when the run's account owns the resource).
* ``per_user``: the run account's ``resource_credential_bindings`` row — the caller for
  caller-identity runs, the agent owner for fixed runs. The binding may outlive grants
  or membership, so every run re-checks resource ``can_use``, the account's active
  membership (when it is not the requester) and credential ``can_use`` (when the account
  does not own it) and fails closed. Missing/unusable connections are collected and
  raised once as ``credential_binding_required`` with the resource ids. Member exit
  already removes the leaver's grants (D18); the membership/``can_use`` re-checks are
  defense in depth for bindings that outlive them. Only ``active`` (and
  ``auth_needed``, which still goes through the existing refresh) credentials resolve.
* Platform rows (system tools/servers) keep the account-owned credential rule.

Values are opened through ``credentials.boundary`` only and live in this resolver (one
``build_tools_config`` call), never on a global cache.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.credential_binding_scope import account_bindings, binding_of
from app.authz.error_codes import authorization_error
from app.authz.org_context import active_memberships
from app.authz.principal import Principal
from app.authz.runtime import runtime_authz
from app.authz.service import AuthzService
from app.authz.service_types import Verdict
from app.credentials.boundary import ResolvedCredential, SecretValues, open_values
from app.credentials.run_subject import RunSubject
from app.error_codes import mcp_server_not_found, tool_not_found
from app.exceptions import AppError, ValidationError
from app.mcp.auth import resolve_mcp_auth
from app.mcp.client import build_headers
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.organization import Organization
from app.models.resource_credential_binding import ResourceCredentialBinding as Binding
from app.models.tool import Tool

logger = logging.getLogger(__name__)
Resource = Tool | McpServer
# ``auth_needed`` keeps the existing flow: OAuth refresh is attempted and fails as before.
USABLE_STATUSES = frozenset({"active", "auth_needed"})


def subject_mismatch() -> ValidationError:
    return ValidationError(
        "CREDENTIAL_SUBJECT_MISMATCH", "credential is not available for this agent run identity"
    )


def _platform(row: Resource) -> bool:
    return bool(row.is_system) and row.org_id is None and row.tenant_id is None


@dataclass(frozen=True, slots=True)
class McpResolution:
    resolved: ResolvedCredential | None
    credentials: dict[str, Any] | None
    headers: dict[str, str]


class RunCredentialResolver:
    def __init__(
        self, db: AsyncSession | None, subject: RunSubject, authz: AuthzService | None = None
    ) -> None:
        self.db, self.subject = db, subject
        self.authz = authz or runtime_authz()
        self.read_only = subject.read_only or db is None
        self.missing: list[dict[str, str]] = []
        self._opened: dict[uuid.UUID, dict[str, Any] | None] = {}

    async def require_resource(self, row: Resource, kind: str, not_found: AppError) -> None:
        """The 11.1 subject's ``can_use`` on a resource the run account does not own."""
        ctx = self.subject.context
        if _platform(row):
            return
        if (row.org_id, row.tenant_id) != (ctx.org_id, ctx.tenant_id):
            raise not_found
        if row.user_id == self.subject.account_user_id or self.read_only:
            return
        await self._require(self.subject.principal, f"{kind}:{row.id}", row.org_id, not_found)

    async def _require(
        self, principal: Principal, obj: str, org_id: uuid.UUID | None, not_found: AppError
    ) -> None:
        await self.authz.require(
            principal,
            "can_use",
            obj,
            min_visible="can_use",
            resource_org_id=org_id,
            legacy=Verdict.NOT_FOUND,  # legacy runs never reach another owner's row
            not_found=not_found,
        )

    async def _shared(self, row: Resource) -> Credential | None:
        if row.credential_id is None:
            return None
        if "credential" not in inspect(row).unloaded:
            credential = row.credential
        elif self.db is not None:
            credential = await self.db.get(Credential, row.credential_id)
        else:
            return None
        if credential is None:
            raise subject_mismatch()
        ctx = self.subject.context
        owner = self.subject.account_user_id if _platform(row) else row.user_id
        if credential.is_system or (
            credential.user_id,
            credential.org_id,
            credential.tenant_id,
        ) != (
            owner,
            ctx.org_id,
            ctx.tenant_id,
        ):
            raise subject_mismatch()
        if credential.status not in USABLE_STATUSES and not self.read_only:
            raise subject_mismatch()
        return credential

    async def bound(self, row: Resource, kind: str) -> Credential | None:
        if self.db is None:  # read-only masking without a session: nothing to resolve
            return None
        ctx, account = self.subject.context, self.subject.account_user_id
        binding = await self.db.scalar(
            select(Binding).where(account_bindings(ctx, account), binding_of(kind, row.id))
        )
        credential = None
        if binding is not None:
            credential = await self.db.get(Credential, binding.credential_id)
        if credential is None:
            return self._missing(kind, row, "missing")
        if self.read_only or await self._usable(credential, row):
            return credential
        return self._missing(kind, row, "unusable")

    def _missing(self, kind: str, row: Resource, reason: str) -> None:
        self.missing.append({"resource_type": kind, "resource_id": str(row.id), "reason": reason})

    async def _usable(self, credential: Credential, row: Resource) -> bool:
        ctx, account = self.subject.context, self.subject.account_user_id
        if (
            credential.is_system
            or credential.status not in USABLE_STATUSES
            or (credential.org_id, credential.tenant_id) != (ctx.org_id, ctx.tenant_id)
            or credential.definition_key != row.credential_definition_key
        ):
            return False
        if account != ctx.user_id and self.db is not None:
            member = await self.db.scalar(
                active_memberships(account).where(Organization.id == ctx.org_id).limit(1)
            )
            if member is None:
                return False
        if credential.user_id == account:
            return True
        principal = Principal(
            "user", account, ctx.org_id, run_source=self.subject.principal.run_source
        )
        try:
            obj = f"credential:{credential.id}"
            await self._require(principal, obj, credential.org_id, authorization_error("not_found"))
        except AppError as denied:
            if denied.status in (403, 404):
                return False
            raise
        return True

    async def _open(self, credential: Credential) -> dict[str, Any] | None:
        if credential.id not in self._opened:
            try:
                self._opened[credential.id] = await (
                    open_values(credential, "redaction")
                    if self.read_only
                    else open_values(credential, "run")
                )
            except Exception:  # noqa: BLE001 — surface as missing creds, never crash chat
                logger.exception("credential decryption failed for credential %s", credential.id)
                self._opened[credential.id] = None
        return self._opened[credential.id]

    async def _credential_for(self, row: Resource, kind: str) -> Credential | None:
        if row.credential_mode == "per_user":
            return await self.bound(row, kind)
        return await self._shared(row)

    def _resolved(
        self, credential: Credential, row: Resource, kind: str, values: dict[str, Any] | None
    ) -> ResolvedCredential:
        mode = "per_user" if row.credential_mode == "per_user" else "shared"
        return ResolvedCredential(
            credential_id=credential.id,
            mode="system" if _platform(row) and mode == "shared" else mode,
            resource_type=kind,
            resource_id=row.id,
            resource_owner_id=row.user_id,
            credential_subject_id=credential.user_id,
            allowed_hosts=tuple(credential.allowed_hosts or ()),
            values=SecretValues(values),
        )

    async def tool(self, tool: Tool) -> ResolvedCredential | None:
        await self.require_resource(tool, "tool", tool_not_found())
        credential = await self._credential_for(tool, "tool")
        if credential is None:
            return None
        return self._resolved(credential, tool, "tool", await self._open(credential))

    async def mcp(self, server: McpServer) -> McpResolution | None:
        """``None`` for a server without URL (not emitted by the runtime)."""
        await self.require_resource(server, "mcp_server", mcp_server_not_found())
        if not server.url:  # never emitted: no binding required, but the shared edge is checked
            if server.credential_mode != "per_user":
                await self._shared(server)
            return None
        credential = await self._credential_for(server, "mcp_server")
        static = dict(server.headers or {})
        if credential is None:
            return McpResolution(None, None, static)
        if self.read_only or self.db is None:
            values = await self._open(credential)
            headers = build_headers(static, values)
        else:
            auth = await resolve_mcp_auth(
                self.db,
                credential_id=credential.id,
                user_id=credential.user_id,
                static_headers=static,
            )
            if auth.error:
                if auth.status == "credential_not_found":
                    raise subject_mismatch()
                raise ValidationError("MCP_CREDENTIAL_AUTH_NEEDED", auth.error)
            values, headers = auth.credentials, auth.headers
        resolved = self._resolved(credential, server, "mcp_server", values)
        return McpResolution(resolved, values, headers)

    def raise_missing(self) -> None:
        if self.missing and not self.read_only:
            raise authorization_error("credential_binding_required", {"resources": self.missing})


__all__ = ["McpResolution", "RunCredentialResolver", "subject_mismatch"]
