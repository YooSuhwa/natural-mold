"""ORM hooks that evict cached MCP tools when a credential identity changes (F-12).

Credential edits (values, status — revocation, ``auth_needed`` — sharing, owner,
allowed hosts, key rotation) and deletes evict that credential's entries; a
per-user binding insert/update/delete evicts the bound account's entries for
that MCP server. Registration is idempotent and happens when the runtime cache
is first imported (nothing is cached before that).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event, inspect

from app.agent_runtime.mcp_tool_cache import invalidate_mcp_tool_cache
from app.models.credential import Credential
from app.models.resource_credential_binding import ResourceCredentialBinding

_IDENTITY_COLUMNS = (
    "data_encrypted",
    "status",
    "user_id",
    "is_shared",
    "allowed_hosts",
    "definition_key",
    "org_id",
    "tenant_id",
)


def _identity_changed(target: Credential) -> bool:
    state = inspect(target)
    return any(
        name in state.attrs and state.attrs[name].history.has_changes()
        for name in _IDENTITY_COLUMNS
    )


def _credential_updated(_mapper: Any, _connection: Any, target: Credential) -> None:
    if _identity_changed(target):
        invalidate_mcp_tool_cache(credential_id=str(target.id))


def _credential_deleted(_mapper: Any, _connection: Any, target: Credential) -> None:
    invalidate_mcp_tool_cache(credential_id=str(target.id))


def _binding_changed(_mapper: Any, _connection: Any, target: ResourceCredentialBinding) -> None:
    if target.resource_type == "mcp_server":
        invalidate_mcp_tool_cache(server_id=str(target.resource_id), account_id=str(target.user_id))


def register_mcp_cache_invalidation() -> None:
    hooks = (
        (Credential, "after_update", _credential_updated),
        (Credential, "after_delete", _credential_deleted),
        (ResourceCredentialBinding, "after_insert", _binding_changed),
        (ResourceCredentialBinding, "after_update", _binding_changed),
        (ResourceCredentialBinding, "after_delete", _binding_changed),
    )
    for model, name, hook in hooks:
        if not event.contains(model, name, hook):
            event.listen(model, name, hook)


__all__ = ["register_mcp_cache_invalidation"]
