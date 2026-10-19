"""Personal account credential types (plan 10.4, Q4; E-05).

The definition's ``personal`` flag is the single source: google_workspace_oauth2,
srt_account, ktx_account, foresttrip_account and mcp_oauth2 (personal by default; the
MCP server "조직 공용 계정" marking that would lift it does not exist yet). Model and
service API keys and the HTTP key/token families are not personal.
"""

import app.credentials.definitions  # noqa: F401  # registers the built-in definitions
from app.credentials.registry import registry


def is_personal_definition(definition_key: str) -> bool:
    """Unknown keys are not personal (nothing to protect without a definition)."""
    definition = registry.get(definition_key)
    return definition is not None and definition.personal


__all__ = ["is_personal_definition"]
