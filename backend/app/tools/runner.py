"""High-level tool runner — turns a :class:`Tool` ORM row into a runner call.

Responsibilities split between this module and the per-definition runner
coroutine:

- **runner.py** loads + decrypts the credential (delegating to
  :mod:`app.credentials.service`), validates required parameters, opens an
  ``httpx.AsyncClient``, and dispatches to the registered runner.
- **definitions/*.py** owns the actual side-effect (HTTP call, API client,
  message format).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.http_ssl import get_outbound_ssl_context
from app.models.credential import Credential
from app.models.tool import Tool
from app.tools.domain import ToolDefinition, ToolRunContext
from app.tools.parameters import FieldDef
from app.tools.registry import ToolRegistry


class ToolRunError(RuntimeError):
    """Raised when a tool can't be executed (config / validation problem)."""


@dataclass
class ToolRunResult:
    """Outcome of :func:`run_tool`. Always JSON-serializable."""

    success: bool
    result: Any = None
    error: str | None = None
    http_status: int | None = None
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "result": self.result,
            "error": self.error,
            "http_status": self.http_status,
            "duration_ms": self.duration_ms,
        }


def _validate_parameters(
    fields: list[FieldDef], values: dict[str, Any]
) -> dict[str, Any]:
    """Apply defaults + required checks. Returns a normalized dict."""

    normalized: dict[str, Any] = {}
    for spec in fields:
        if spec.name in values and values[spec.name] is not None:
            normalized[spec.name] = values[spec.name]
        elif spec.default is not None:
            normalized[spec.name] = spec.default
        elif spec.required:
            raise ToolRunError(
                f"required parameter '{spec.name}' is missing"
            )
    # Pass through unknown keys so runners can accept extras (e.g. dynamic
    # query params) without forcing every key into the FieldDef list.
    for k, v in values.items():
        normalized.setdefault(k, v)
    return normalized


async def _load_credential_payload(
    db: AsyncSession, credential_id: Any
) -> dict[str, Any] | None:
    if credential_id is None:
        return None
    row = (
        await db.execute(select(Credential).where(Credential.id == credential_id))
    ).scalar_one_or_none()
    if row is None:
        raise ToolRunError(
            f"credential {credential_id} not found (was it deleted?)"
        )
    return await credential_service.decrypt_with_external(row.data_encrypted)


async def run_tool(
    *,
    db: AsyncSession,
    tool: Tool,
    registry: ToolRegistry,
    runtime_args: dict[str, Any] | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> ToolRunResult:
    """Execute ``tool`` once. Returns a structured result envelope.

    The caller (router) is responsible for HTTP-level error mapping; this
    function returns ``success=False`` instead of raising for runtime errors,
    matching the chat runtime's "tool returns error string" pattern.
    """

    definition = registry.get(tool.definition_key)
    if definition is None:
        return ToolRunResult(
            success=False,
            error=f"unknown tool definition '{tool.definition_key}'",
        )
    if definition.runner is None:
        return ToolRunResult(
            success=False,
            error=f"tool definition '{tool.definition_key}' has no runner",
        )

    merged: dict[str, Any] = {**(tool.parameters or {})}
    if runtime_args:
        merged.update(runtime_args)

    try:
        validated = _validate_parameters(definition.parameters, merged)
        credentials = await _load_credential_payload(db, tool.credential_id)
    except ToolRunError as exc:
        return ToolRunResult(success=False, error=str(exc))

    own_client = http_client is None
    client = http_client or httpx.AsyncClient(
        timeout=30.0,
        verify=get_outbound_ssl_context(),
    )
    started = time.monotonic()
    http_status: int | None = None
    try:
        ctx = ToolRunContext(
            parameters=validated,
            credentials=credentials,
            http_client=client,
        )
        result = await definition.runner(ctx)
        if isinstance(result, dict):
            maybe_status = result.get("http_status")
            if isinstance(maybe_status, int):
                http_status = maybe_status
        return ToolRunResult(
            success=True,
            result=result,
            http_status=http_status,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except httpx.HTTPStatusError as exc:
        return ToolRunResult(
            success=False,
            error=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            http_status=exc.response.status_code,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except httpx.HTTPError as exc:
        return ToolRunResult(
            success=False,
            error=f"network error: {exc}",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception as exc:  # noqa: BLE001 — runner errors are user-visible
        return ToolRunResult(
            success=False,
            error=f"{type(exc).__name__}: {exc}",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    finally:
        if own_client:
            await client.aclose()


def _expose_definition(definition: ToolDefinition) -> dict[str, Any]:
    """Public helper used by the router catalog endpoint."""

    return definition.serialize()


__all__ = [
    "ToolRunError",
    "ToolRunResult",
    "_expose_definition",
    "run_tool",
]
