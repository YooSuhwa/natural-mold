"""Runtime resolver for MCP connection env_vars templates.

ADR-008 §2 runtime interpreter. Resolves `${credential.<field>}` template
references in `connection.extra_config.env_vars` to the credential's decrypted
value at chat-build time. New inputs are already restricted to template-only
by the Pydantic schema, but M2 migration opportunistically ported legacy
plaintext values, so the runtime accepts both shapes until M6.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from app.exceptions import AppError
from app.models.credential import Credential
from app.services.credential_service import resolve_credential_data

logger = logging.getLogger(__name__)

_ENV_VAR_TEMPLATE = re.compile(r"^\$\{credential\.([a-z_][a-z0-9_]*)\}$")


class ToolConfigError(AppError):
    """Tool runtime configuration invalid.

    Surfaces as 400 via main.py AppError handler instead of the generic 500
    path. Raised from chat_service.build_tools_config / env_var_resolver when
    a connection-backed MCP tool has stale or inconsistent state (missing
    URL, broken `${credential.x}` reference, ownership mismatch, etc.).
    """

    def __init__(self, message: str, *, code: str = "TOOL_CONFIG_ERROR"):
        super().__init__(code=code, message=message, status=400)


def resolve_env_vars(
    env_vars: Any,
    credential: Credential | None,
    *,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve `${credential.<field>}` templates in env_vars against credential data.

    - Template match → credential.data_decrypted[field]
    - Plaintext string → emit warning, return verbatim (M2 legacy tolerance;
      will be rejected after M6)
    - Template referencing a missing field → ToolConfigError
    - Template without a credential → ToolConfigError
    - Non-dict `env_vars` input → ToolConfigError (DB-level shape defense)

    `context` is a small diagnostic payload (conn_id, tool_name, …) passed into
    warning logs so M6 cutoff can grep remaining plaintext occurrences.
    """
    if env_vars is None:
        return {}
    if not isinstance(env_vars, dict):
        raise ToolConfigError(
            f"extra_config.env_vars must be a dict (got {type(env_vars).__name__})"
        )
    cred_data = resolve_credential_data(credential) if credential else {}
    resolved: dict[str, Any] = {}
    for key, value in env_vars.items():
        if not isinstance(value, str):
            resolved[key] = value
            continue
        match = _ENV_VAR_TEMPLATE.match(value)
        if match:
            field = match.group(1)
            if field not in cred_data:
                raise ToolConfigError(
                    f"MCP env_var '{key}' references credential.{field}, not present in credential"
                )
            resolved[key] = cred_data[field]
        else:
            logger.warning(
                "MCP env_var '%s' is not a template; plaintext value "
                "used (legacy M2 tolerance — rejected after M6) "
                "context=%s",
                key,
                context or {},
            )
            resolved[key] = value
    return resolved


def assert_connection_ownership(
    *,
    tool_user_id: uuid.UUID | None,
    connection_user_id: uuid.UUID,
    connection_id: uuid.UUID,
    tool_name: str,
) -> None:
    """Guard against cross-tenant credential leaks via tool→connection→credential chain.

    M1 POST/PATCH already validates connection/credential ownership, but a
    hand-crafted DML or M6 migration mistake could end up with mismatched
    user_ids. Raising here keeps `build_tools_config` from silently decrypting
    another user's credential into an LLM call once multi-user auth lands.
    """
    if tool_user_id is not None and tool_user_id != connection_user_id:
        raise ToolConfigError(
            f"Tool '{tool_name}' connection {connection_id} owner mismatch with tool owner"
        )


def assert_credential_ownership(
    *,
    connection_user_id: uuid.UUID,
    credential: Credential | None,
    connection_id: uuid.UUID,
) -> None:
    if credential is None:
        return
    if credential.user_id != connection_user_id:
        raise ToolConfigError(
            f"Credential {credential.id} on connection {connection_id} is owned by a different user"
        )
