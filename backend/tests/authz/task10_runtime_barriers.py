"""Barrier models, seam installs and authority mutations for Task 10 runtime tests.

Dialect-agnostic (shared by the ordinary authority matrix and the native modules).

A ``BarrierModel`` parks inside its first provider attempt — i.e. inside the
lease ``LeaseSendGuard`` permit, whose authority transaction has already
released the canonical parent locks and its connection (lock -> decide ->
release -> I/O) — until the test releases it, and may run a hook right after
the send returned. Writers therefore commit while it is parked and the next
dispatch boundary observes them. ``park_after`` parks any locking service
right after it returns (its transaction still holding its row locks), giving
real two-connection barriers with a deterministic winner.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatResult
from pydantic import PrivateAttr
from sqlalchemy import Update, update

from app.agent_api import prepared_execution
from app.agent_runtime import prepared_run_inputs
from app.agent_runtime.lease_bound_model import LeaseSendGuard
from app.agent_runtime.lease_model_factory import LeaseModelSpec
from app.authz.org_context import OrgContext
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from tests.authz import prepared_entry_testkit as ekit
from tests.authz import prepared_materialization_testkit as kit

Hook = Callable[[], Awaitable[None]]
BLOCKED_PROBE_SECONDS = 0.5
FINISH_SECONDS = 30.0


@dataclass
class Gate:
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def wait_entered(self) -> None:
        await asyncio.wait_for(self.entered.wait(), FINISH_SECONDS)


class BarrierModel(ekit.RecordingModel):
    _gate: Gate | None = PrivateAttr(default=None)
    _after: Hook | None = PrivateAttr(default=None)

    async def _agenerate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> ChatResult:
        ekit.SEEN.append(list(messages))
        gate, self._gate = self._gate, None

        async def attempt() -> ChatResult:
            kit.SENDS.append(self._tag)
            if gate is not None:
                gate.entered.set()
                await gate.release.wait()
            return self._generate(messages, stop=stop, **kw)

        result = await self._guard.send(attempt)
        hook, self._after = self._after, None
        if hook is not None:
            await hook()
        return result


@dataclass
class Barriers(kit.ScriptedFactory):
    gates: dict[str, Gate] = field(default_factory=dict)
    hooks: dict[str, Hook] = field(default_factory=dict)

    def __call__(self, spec: LeaseModelSpec, guard: LeaseSendGuard) -> kit.FakeOwned:
        model = BarrierModel(responses=list(self.scripts[spec.model_name]))
        model._guard, model._tag = guard, spec.model_name
        model._gate = self.gates.get(spec.model_name)
        model._after = self.hooks.get(spec.model_name)
        self.built[spec.model_name] = kit.FakeOwned(model)
        return self.built[spec.model_name]


def install_chat(
    monkeypatch: pytest.MonkeyPatch,
    harness: ekit.Harness,
    factory: kit.ScriptedFactory,
    server: Any = None,
    **overrides: Any,
) -> None:
    seams = replace(kit.seams(factory, server or harness.server), **overrides)
    monkeypatch.setattr(prepared_run_inputs, "materializer_seams", lambda: seams)


def install_api(monkeypatch: pytest.MonkeyPatch, factory: kit.ScriptedFactory, server: Any) -> None:
    current = prepared_execution.SEAMS
    seams = replace(current, materializer=kit.seams(factory, server))
    monkeypatch.setattr(prepared_execution, "SEAMS", seams)


def install_trigger(monkeypatch: pytest.MonkeyPatch, factory: kit.ScriptedFactory, server: Any):
    monkeypatch.setattr(
        prepared_run_inputs, "materializer_seams", lambda: kit.seams(factory, server)
    )


def park_after(monkeypatch: pytest.MonkeyPatch, module: object, name: str) -> Gate:
    """Park the first caller of ``module.name`` right after it returns (locks still held)."""

    gate, original = Gate(), getattr(module, name)

    async def parked(*args: Any, **kwargs: Any) -> Any:
        result = await original(*args, **kwargs)
        if not gate.entered.is_set():
            gate.entered.set()
            await gate.release.wait()
        return result

    monkeypatch.setattr(module, name, parked)
    return gate


async def assert_blocked(*tasks: asyncio.Task[Any]) -> None:
    await asyncio.sleep(BLOCKED_PROBE_SECONDS)
    assert not any(task.done() for task in tasks), [t for t in tasks if t.done()]


async def finish[T](task: Awaitable[T]) -> T:
    return await asyncio.wait_for(task, FINISH_SECONDS)


def tool_call(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def _tenant(c: OrgContext) -> Update:
    return update(Tenant).where(Tenant.id == c.tenant_id)


def _company_member(c: OrgContext) -> Update:
    return update(TenantMember).where(
        TenantMember.tenant_id == c.tenant_id, TenantMember.user_id == c.user_id
    )


def _org_member(c: OrgContext) -> Update:
    return update(OrganizationMember).where(
        OrganizationMember.org_id == c.org_id, OrganizationMember.user_id == c.user_id
    )


# name -> (statement, dispatch denial code). Organization-only revocation never
# touches TenantMember and company exit never touches OrganizationMember.
MUTATIONS: dict[str, tuple[Callable[[OrgContext], Update], str]] = {
    "tenant_suspended": (lambda c: _tenant(c).values(status="suspended"), "tenant_suspended"),
    "company_member_suspended": (
        lambda c: _company_member(c).values(status="suspended"),
        "forbidden",
    ),
    "company_member_removed": (lambda c: _company_member(c).values(status="removed"), "forbidden"),
    "org_member_suspended": (lambda c: _org_member(c).values(status="suspended"), "forbidden"),
    "org_member_removed": (lambda c: _org_member(c).values(status="removed"), "forbidden"),
}


def archived_org(context: OrgContext) -> Update:
    """Raw archived stamp (no cascade) for seam tests that isolate the dispatch permit."""

    stamp = datetime.now(UTC)
    statement = update(Organization).where(Organization.id == context.org_id)
    return statement.values(status="archived", archived_at=stamp)
