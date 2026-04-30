"""Hook framework — registry order, failure isolation, builtin hooks."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.credentials import service as credential_service
from app.hooks import register_default_hooks
from app.hooks.base import CustomHook, HookContext, HookResult
from app.hooks.builtin.audit_hook import AuditHook
from app.hooks.builtin.logging_hook import LoggingHook
from app.hooks.registry import HookRegistry
from app.models.credential_audit_log import CredentialAuditLog
from app.models.user import User
from tests.conftest import TEST_USER_ID, TestSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(
    *,
    kind: str = "agent_invoke",
    credential_id: uuid.UUID | None = None,
    metadata: dict | None = None,
) -> HookContext:
    return HookContext(
        request_id=str(uuid.uuid4()),
        kind=kind,  # type: ignore[arg-type]  # tests cover both literals
        user_id=TEST_USER_ID,
        started_at=datetime.now(UTC).replace(tzinfo=None),
        credential_id=credential_id,
        metadata=metadata or {},
    )


class _TraceHook(CustomHook):
    """Test hook that records every call into a shared list."""

    def __init__(self, name: str, sink: list[tuple[str, str]]) -> None:
        self.name = name
        self._sink = sink

    async def async_pre_call_hook(self, ctx: HookContext) -> None:
        self._sink.append((self.name, "pre"))

    async def async_post_call_hook(self, ctx: HookContext, result: HookResult) -> None:
        self._sink.append((self.name, "post"))

    async def async_failure_hook(self, ctx: HookContext, error: Exception) -> None:
        self._sink.append((self.name, "failure"))


class _BoomHook(CustomHook):
    name = "boom"

    async def async_pre_call_hook(self, ctx: HookContext) -> None:
        raise RuntimeError("pre boom")

    async def async_post_call_hook(self, ctx: HookContext, result: HookResult) -> None:
        raise RuntimeError("post boom")

    async def async_failure_hook(self, ctx: HookContext, error: Exception) -> None:
        raise RuntimeError("failure boom")


# ---------------------------------------------------------------------------
# Registration / ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_dispatches_in_registration_order() -> None:
    registry = HookRegistry()
    sink: list[tuple[str, str]] = []
    registry.register(_TraceHook("first", sink))
    registry.register(_TraceHook("second", sink))

    ctx = _make_ctx()
    await registry.run_pre(ctx)
    await registry.run_post(ctx, HookResult(duration_ms=10))
    await registry.run_failure(ctx, RuntimeError("x"))

    assert sink == [
        ("first", "pre"),
        ("second", "pre"),
        ("first", "post"),
        ("second", "post"),
        ("first", "failure"),
        ("second", "failure"),
    ]


def test_register_replaces_by_name_idempotent() -> None:
    registry = HookRegistry()
    sink: list[tuple[str, str]] = []
    a = _TraceHook("dup", sink)
    b = _TraceHook("dup", sink)
    registry.register(a)
    registry.register(b)
    assert registry.all() == [b]


def test_register_default_hooks_idempotent() -> None:
    """Running the bootstrap twice yields a stable order without duplicates."""

    from app.hooks import hooks

    hooks.clear()
    register_default_hooks()
    register_default_hooks()
    names = [h.name for h in hooks.all()]
    assert names == ["logging_hook", "audit_hook"]


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_hook_failure_doesnt_block_others() -> None:
    """A throwing hook is logged + skipped; siblings still execute."""

    registry = HookRegistry()
    sink: list[tuple[str, str]] = []
    registry.register(_BoomHook())
    registry.register(_TraceHook("survivor", sink))

    ctx = _make_ctx()
    await registry.run_pre(ctx)
    await registry.run_post(ctx, HookResult(duration_ms=5))
    await registry.run_failure(ctx, RuntimeError("downstream"))

    assert ("survivor", "pre") in sink
    assert ("survivor", "post") in sink
    assert ("survivor", "failure") in sink


@pytest.mark.asyncio
async def test_disabled_hook_is_skipped() -> None:
    registry = HookRegistry()
    sink: list[tuple[str, str]] = []
    enabled = _TraceHook("on", sink)
    disabled = _TraceHook("off", sink)
    disabled.enabled = False
    registry.register(enabled)
    registry.register(disabled)

    await registry.run_pre(_make_ctx())
    assert sink == [("on", "pre")]


# ---------------------------------------------------------------------------
# Builtin hooks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logging_hook_no_op_against_dispatch() -> None:
    """Smoke test — :class:`LoggingHook` runs all three lifecycle methods."""

    registry = HookRegistry()
    registry.register(LoggingHook())
    ctx = _make_ctx()
    await registry.run_pre(ctx)
    await registry.run_post(ctx, HookResult(duration_ms=12, tokens_in=1, tokens_out=2))
    await registry.run_failure(ctx, RuntimeError("x"))


@pytest.mark.asyncio
async def test_audit_hook_writes_row_only_when_credential_present(monkeypatch) -> None:
    """``AuditHook`` is a no-op for non-credentialed calls."""

    monkeypatch.setattr("app.hooks.builtin.audit_hook.async_session", TestSession)

    async with TestSession() as db:
        db.add(User(id=TEST_USER_ID, email="hooks@test", name="hooks"))
        await db.commit()
        cred = await credential_service.create(
            db,
            user_id=TEST_USER_ID,
            definition_key="openai",
            name="hook cred",
            data={"api_key": "sk-abc"},
        )
        await db.commit()
        cred_id = cred.id

    hook = AuditHook()

    # No credential → no row written.
    bare_ctx = _make_ctx()
    await hook.async_post_call_hook(bare_ctx, HookResult(duration_ms=3))

    async with TestSession() as db:
        rows = (
            await db.execute(
                select(CredentialAuditLog).where(CredentialAuditLog.action == "invoke")
            )
        ).scalars().all()
        assert rows == []

    # With credential → exactly one row, action=invoke, source=runtime.
    cred_ctx = _make_ctx(
        kind="tool_call",
        credential_id=cred_id,
        metadata={"tool_name": "naver_blog"},
    )
    await hook.async_post_call_hook(
        cred_ctx, HookResult(duration_ms=42, tokens_in=10, tokens_out=20)
    )

    async with TestSession() as db:
        rows = (
            await db.execute(
                select(CredentialAuditLog).where(CredentialAuditLog.action == "invoke")
            )
        ).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.credential_id == cred_id
        assert row.actor_user_id == TEST_USER_ID
        assert row.source == "runtime"
        assert row.log_metadata is not None
        assert row.log_metadata["phase"] == "post"
        assert row.log_metadata["kind"] == "tool_call"
        assert row.log_metadata["duration_ms"] == 42
        assert row.log_metadata["tokens_in"] == 10
        assert row.log_metadata["meta_tool_name"] == "naver_blog"


@pytest.mark.asyncio
async def test_audit_hook_records_failure_with_error_message(monkeypatch) -> None:
    monkeypatch.setattr("app.hooks.builtin.audit_hook.async_session", TestSession)

    async with TestSession() as db:
        db.add(User(id=TEST_USER_ID, email="hooks@test", name="hooks"))
        await db.commit()
        cred = await credential_service.create(
            db,
            user_id=TEST_USER_ID,
            definition_key="openai",
            name="failure cred",
            data={"api_key": "sk-x"},
        )
        await db.commit()
        cred_id = cred.id

    hook = AuditHook()
    await hook.async_failure_hook(
        _make_ctx(kind="mcp_call", credential_id=cred_id),
        RuntimeError("upstream 502"),
    )

    async with TestSession() as db:
        rows = (
            await db.execute(
                select(CredentialAuditLog).where(CredentialAuditLog.action == "invoke")
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].error == "upstream 502"
        assert rows[0].log_metadata is not None
        assert rows[0].log_metadata["phase"] == "failure"
