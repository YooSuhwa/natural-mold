"""B-13: derived work (hooks, audits) carries the run's company into its own session."""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.agent_runtime import tool_factory
from app.hooks.base import HookContext, HookResult
from app.hooks.builtin import audit_hook
from app.mcp import client as mcp_client

TENANT = uuid.UUID("00000000-0000-0000-0000-0000000000d1")
USER = uuid.UUID("00000000-0000-0000-0000-0000000000d2")


def test_tool_hook_context_carries_the_runs_company() -> None:
    ctx = tool_factory._build_tool_hook_context(  # noqa: SLF001
        user_uuid=USER,
        tenant_uuid=TENANT,
        tool_uuid=None,
        credential_uuid=None,
        agent_uuid=None,
        tool_name="t",
        definition_key="k",
    )
    assert ctx is not None and ctx.tenant_id == TENANT


def test_tool_hook_context_is_skipped_without_a_company() -> None:
    ctx = tool_factory._build_tool_hook_context(  # noqa: SLF001
        user_uuid=USER,
        tenant_uuid=None,
        tool_uuid=None,
        credential_uuid=None,
        agent_uuid=None,
        tool_name="t",
        definition_key="k",
    )
    assert ctx is None


def test_mcp_hook_context_carries_the_callers_company() -> None:
    ctx = mcp_client._build_mcp_hook_context(  # noqa: SLF001
        user_id=USER,
        tenant_id=TENANT,
        mcp_server_id=None,
        credential_id=None,
        url=None,
        transport="streamable_http",
    )
    assert ctx is not None and ctx.tenant_id == TENANT
    assert (
        mcp_client._build_mcp_hook_context(  # noqa: SLF001
            user_id=USER,
            tenant_id=None,
            mcp_server_id=None,
            credential_id=None,
            url=None,
            transport="stdio",
        )
        is None
    )


def test_hook_context_requires_a_company() -> None:
    parameter = inspect.signature(HookContext).parameters["tenant_id"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.annotation in {"uuid.UUID", uuid.UUID}


async def test_audit_hook_writes_in_the_hook_company(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[dict[str, str]] = []

    class _Session:
        info: dict[str, Any]

        def __init__(self) -> None:
            self.info = {}

        async def __aenter__(self) -> _Session:
            opened.append(self.info["moldy_db_scope"])
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def commit(self) -> None:
            return None

    async def _write_audit_log(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(audit_hook, "async_session", _Session)
    monkeypatch.setattr("app.credentials.service.write_audit_log", _write_audit_log)
    ctx = HookContext(
        request_id="r",
        kind="tool_call",
        user_id=USER,
        started_at=datetime.now(UTC),
        credential_id=uuid.uuid4(),
        tenant_id=TENANT,
    )
    await audit_hook.AuditHook().async_post_call_hook(ctx, HookResult(duration_ms=1))
    assert opened == [{"app.tenant_id": str(TENANT), "app.user_id": str(USER)}]
