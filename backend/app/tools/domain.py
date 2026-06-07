"""Tool definition dataclass — the per-key schema + runner bundle.

A :class:`ToolDefinition` is registered once per logical tool family
(``http_request``, ``naver_search_blog``, ...). It declares the parameter
fields the user fills in, the credential definition keys it accepts, and the
async runner that executes a single invocation.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.tools.parameters import FieldDef
from app.tools.risk import DecisionType, ToolRiskLevel, risk_metadata_dict


@dataclass
class ToolRunContext:
    """Per-invocation context handed to a :class:`ToolDefinition` runner.

    ``parameters`` is the merged dict of stored ``Tool.parameters`` and
    runtime arguments supplied by the caller. ``credentials`` is the decrypted
    payload of the linked ``Credential`` (or ``None`` when no credential is
    attached). ``http_client`` is a shared httpx client the runner may use to
    avoid per-call connection setup.
    """

    parameters: dict[str, Any]
    credentials: dict[str, Any] | None
    http_client: httpx.AsyncClient


# A runner is a coroutine that performs the side-effect and returns an
# arbitrary JSON-serializable value (string, dict, list).
ToolRunner = Callable[[ToolRunContext], Awaitable[Any]]


@dataclass
class ToolDefinition:
    """Per-key tool schema and runner."""

    key: str
    display_name: str
    description: str
    icon_id: str | None = None
    category: str = "general"
    parameters: list[FieldDef] = field(default_factory=list)
    # Credential definition keys this tool can use. An empty list means the
    # tool does not require a credential. The first entry is treated as the
    # preferred default by the UI.
    credential_definition_keys: list[str] = field(default_factory=list)
    risk_level: ToolRiskLevel | str = ToolRiskLevel.READ_ONLY
    requires_approval: bool | None = None
    allowed_decisions: tuple[DecisionType, ...] = ()
    trigger_safe: bool | None = None
    risk_reason: str | None = None
    runner: ToolRunner | None = None

    def serialize(self) -> dict[str, Any]:
        """JSON-friendly representation for the API catalog endpoint."""

        return {
            "key": self.key,
            "display_name": self.display_name,
            "description": self.description,
            "icon_id": self.icon_id,
            "category": self.category,
            "parameters": [p.serialize() for p in self.parameters],
            "credential_definition_keys": list(self.credential_definition_keys),
            "requires_credential": bool(self.credential_definition_keys),
            "risk": risk_metadata_dict(
                self.risk_level,
                requires_approval=self.requires_approval,
                allowed_decisions=list(self.allowed_decisions),
                trigger_safe=self.trigger_safe,
                reason=self.risk_reason,
            ),
        }


__all__ = ["ToolDefinition", "ToolRunContext", "ToolRunner"]
