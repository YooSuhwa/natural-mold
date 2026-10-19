"""Frozen MCP descriptors for a prepared lease (parent Task 10, internal I13).

Each selected MCP tool is frozen with its opaque server id, original MCP tool
name, exposed tool name, input-schema digest, a digest of the *effective*
transport headers plus injected auth arguments, and secret-reference IDs, so
the lease digest binds exactly what was discovered. Secret values enter the
digest only through a process-local keyed HMAC (leases are process-local), so
the digest is neither a brute-forceable nor a cross-process-linkable secret
fingerprint.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from langchain_core.tools import BaseTool

if TYPE_CHECKING:
    from app.agent_runtime.mcp_strict_loader import StrictMcpTool

_PROCESS_KEY = secrets.token_bytes(32)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=repr).encode()
    ).hexdigest()


def secret_material_digest(*sources: Mapping[str, Any] | None) -> str:
    """Digest names plus keyed-HMAC values of headers/auth arguments."""

    keyed = [
        {
            str(name): hmac.new(_PROCESS_KEY, repr(value).encode(), hashlib.sha256).hexdigest()
            for name, value in (source or {}).items()
        }
        for source in sources
    ]
    return _digest(keyed)


@dataclass(frozen=True, slots=True)
class PreparedMcpTool:
    descriptor_id: str
    server_key: str
    mcp_tool_name: str
    tool_name: str
    schema_digest: str
    header_digest: str
    secret_reference_ids: tuple[str, ...]
    tool: BaseTool = field(repr=False, compare=False)

    def digest_fields(self) -> dict[str, Any]:
        return {
            "descriptor_id": self.descriptor_id,
            "server_key": self.server_key,
            "mcp_tool_name": self.mcp_tool_name,
            "tool_name": self.tool_name,
            "schema_digest": self.schema_digest,
            "header_digest": self.header_digest,
            "secret_reference_ids": list(self.secret_reference_ids),
        }


@dataclass(frozen=True, slots=True)
class PreparedMcpResources:
    tools: tuple[PreparedMcpTool, ...]
    discovery_count: int

    @property
    def digest(self) -> str:
        return _digest([item.digest_fields() for item in self.tools])

    @property
    def descriptor_ids(self) -> frozenset[str]:
        return frozenset(item.descriptor_id for item in self.tools)


def freeze_tool(item: StrictMcpTool) -> PreparedMcpTool:
    tool = item.tool
    schema = tool.args_schema if isinstance(tool.args_schema, dict) else str(tool.args_schema)
    secret_ids = tuple(
        sorted(
            str(value)
            for key in ("credential_id", "mcp_credential_id", "connection_id")
            if (value := item.config.get(key))
        )
    )
    return PreparedMcpTool(
        descriptor_id=f"mcp:{item.server_id}:{item.mcp_tool_name}",
        server_key=item.server_id,
        mcp_tool_name=item.mcp_tool_name,
        tool_name=tool.name,
        schema_digest=_digest(schema),
        header_digest=secret_material_digest(item.headers, item.auth),
        secret_reference_ids=secret_ids,
        tool=tool,
    )
