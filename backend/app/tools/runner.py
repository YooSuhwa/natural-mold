"""High-level tool runner — turns a :class:`Tool` ORM row into a runner call.

Responsibilities split between this module and the per-definition runner
coroutine:

- **runner.py** takes the credential resolved for the caller (shared: the tool's,
  per_user: the caller's own connection; :mod:`app.credentials.run_resolution`),
  validates required parameters, opens an ``outbound_http_client`` (SSRF guard;
  the credential's allowed hosts bound per request), and dispatches to the runner.
- **definitions/*.py** owns the actual side-effect (HTTP call, API client,
  message format).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.credentials.boundary import ResolvedCredential
from app.credentials.run_resolution import RunCredentialResolver
from app.credentials.run_subject import subject_for_caller
from app.error_codes import credential_not_found
from app.exceptions import ValidationError
from app.models.tool import Tool
from app.outbound.client import outbound_http_client
from app.outbound.destination import CredentialDestination, bind_destination
from app.tools.domain import ToolDefinition, ToolRunContext
from app.tools.execution_scope import ToolRunOptions, ToolRunTarget, load_execution_tool
from app.tools.parameters import FieldDef


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


def _validate_parameters(fields: list[FieldDef], values: dict[str, Any]) -> dict[str, Any]:
    """Apply defaults + required checks. Returns a normalized dict."""

    normalized: dict[str, Any] = {}
    for spec in fields:
        if spec.name in values and values[spec.name] is not None:
            normalized[spec.name] = values[spec.name]
        elif spec.default is not None:
            normalized[spec.name] = spec.default
        elif spec.required:
            raise ToolRunError(f"required parameter '{spec.name}' is missing")
    # Pass through unknown keys so runners can accept extras (e.g. dynamic
    # query params) without forcing every key into the FieldDef list.
    for k, v in values.items():
        normalized.setdefault(k, v)
    return normalized


async def _load_credential(
    db: AsyncSession, tool: Tool, context: OrgContext
) -> ResolvedCredential | None:
    resolver = RunCredentialResolver(db, subject_for_caller(context))
    try:
        resolved = await resolver.tool(tool)
    except ValidationError as error:  # not the tool owner's: the neutral 404 as before
        if error.code != "CREDENTIAL_SUBJECT_MISMATCH":
            raise
        raise credential_not_found() from None
    if resolver.missing:
        raise ToolRunError("credential_binding_required: 이 도구에 내 계정을 먼저 연결해 주세요")
    return resolved


async def run_tool(
    db: AsyncSession, target: ToolRunTarget, options: ToolRunOptions
) -> ToolRunResult:
    """Execute ``tool`` once. Returns a structured result envelope.

    The caller (router) is responsible for HTTP-level error mapping; this
    function returns ``success=False`` instead of raising for runtime errors,
    matching the chat runtime's "tool returns error string" pattern.
    """

    tool = await load_execution_tool(db, target)
    definition = options.registry.get(tool.definition_key)
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
    if options.runtime_args:
        merged.update(options.runtime_args)

    try:
        validated = _validate_parameters(definition.parameters, merged)
        resolved = await _load_credential(db, tool, target.context)
    except ToolRunError as exc:
        return ToolRunResult(success=False, error=str(exc))

    credentials = resolved.values.reveal() if resolved else None
    # The caller's own direct use (11.9): allowed hosts apply; empty hosts are allowed.
    destination = CredentialDestination(resolved.allowed_hosts, True) if resolved else None
    own_client = options.http_client is None
    client = options.http_client or outbound_http_client(timeout=30.0, ssrf_guard=True)
    started = time.monotonic()
    http_status: int | None = None
    try:
        ctx = ToolRunContext(
            parameters=validated,
            credentials=credentials,
            http_client=client,
        )
        with bind_destination(destination):
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
