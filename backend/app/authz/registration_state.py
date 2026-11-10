"""Whether a tool or MCP server is registered for use (tool-governance-plan 3.7).

Under registration review a member's tool or server waits ``pending`` until an
administrator approves it; a server whose connection changed since it was approved waits
again. Only an approved row is attached to agents and loaded into runs. Kept apart from
``app.approvals.registrations`` (the request and its decision) so the run and attach paths
read the state without importing the approval machinery.
"""

from __future__ import annotations

import hashlib
import json

from app.models.mcp_server import McpServer
from app.models.tool import Tool

Resource = McpServer | Tool


def fingerprint(server: McpServer) -> str:
    """Hash of what decides where a server's calls go and how they authenticate."""
    value = {
        "transport": server.transport,
        "url": server.url,
        "command": server.command,
        "args": list(server.args or []),
        "credential_id": str(server.credential_id) if server.credential_id else None,
        "credential_mode": server.credential_mode,
        "headers": sorted((server.headers or {}).keys()),
        "env": sorted((server.env_vars or {}).keys()),
    }
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def registered(resource: Resource) -> bool:
    """Agents may attach and runs may load it: approved, and still what was approved."""
    if resource.registration_state != "approved":
        return False
    if isinstance(resource, McpServer) and resource.registration_fingerprint is not None:
        return resource.registration_fingerprint == fingerprint(resource)
    return True


def effective_state(resource: Resource) -> str:
    """The state as people see it: an approved server that changed since waits again."""
    if resource.registration_state == "approved" and not registered(resource):
        return "pending"
    return resource.registration_state
