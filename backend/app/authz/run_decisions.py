"""Run decision memo: decide before the locks, reuse under them (plan 8.10, 11.2; F-06).

``run_decision_scope()`` merges duplicate decisions of one request or run (the tree
loader, the conversation hydration and the planner all ask the caller's
``agent#can_run``). ``fresh_run_decision_scope()`` (prepared leases, the I17 pattern)
opens a new memo that ``seal()`` closes once decisions are made: afterwards a missing
answer is a denial and OpenFGA is never called, so no check runs while the
materialization holds its DB locks. Keys carry the run source, which conditional
tuples (``allowed_run_source``) depend on.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from uuid import UUID

from app.authz import denials
from app.authz.client import AuthzUnavailable
from app.authz.principal import Principal
from app.authz.service import AuthzService, Verdict
from app.exceptions import AppError

_Key = tuple[str, str, str, str]  # principal, run source, permission, object


@dataclass(slots=True)
class RunDecisionMemo:
    answers: dict[_Key, bool] = field(default_factory=dict)
    sealed: bool = False
    asked_after_seal: int = 0  # misses answered "no" without OpenFGA (fail closed)
    unavailable: bool = False  # OpenFGA was down while deciding: sealed misses stay 503

    def seal(self) -> None:
        self.sealed = True


_memo: ContextVar[RunDecisionMemo | None] = ContextVar("run_decision_memo", default=None)


def open_request_memo() -> None:
    """A route dependency's memo: lives for that request's task only."""
    _memo.set(RunDecisionMemo())


@contextmanager
def fresh_run_decision_scope() -> Iterator[RunDecisionMemo]:
    memo = RunDecisionMemo()
    token = _memo.set(memo)
    try:
        yield memo
    finally:
        _memo.reset(token)


@contextmanager
def run_decision_scope() -> Iterator[RunDecisionMemo]:
    """Join the active memo (a request's, a sealed lease's), or open one."""
    memo = _memo.get()
    if memo is not None:
        yield memo
        return
    with fresh_run_decision_scope() as fresh:
        yield fresh


def _key(p: Principal, permission: str, obj: str) -> _Key:
    return (p.fga(), p.run_source, permission, obj)


async def batch_decide(
    authz: AuthzService, p: Principal, items: Sequence[tuple[str, str]]
) -> dict[tuple[str, str], bool]:
    """``authz.batch_check`` through the memo; raises ``AuthzUnavailable`` as it does."""
    memo = _memo.get()
    if memo is None:
        return await authz.batch_check(p, items)
    missing = [item for item in dict.fromkeys(items) if _key(p, *item) not in memo.answers]
    if missing and memo.sealed:
        memo.asked_after_seal += len(missing)
        if memo.unavailable:
            raise AuthzUnavailable("sealed run decisions")
    elif missing:
        try:
            answers = await authz.batch_check(p, missing)
        except AuthzUnavailable:
            memo.unavailable = True
            raise
        memo.answers.update({_key(p, *item): ok for item, ok in answers.items()})
    return {item: memo.answers.get(_key(p, *item), False) for item in items}


async def require_decided(
    authz: AuthzService,
    p: Principal,
    permission: str,
    obj: str,
    *,
    min_visible: str,
    resource_org_id: UUID | None,
    legacy: Verdict,
    not_found: AppError,
) -> None:
    """``AuthzService.require`` for run paths: memoized while the type is enforced."""
    if not authz.enforces(obj.partition(":")[0]):
        await authz.require(
            p,
            permission,
            obj,
            min_visible=min_visible,
            resource_org_id=resource_org_id,
            legacy=legacy,
            not_found=not_found,
        )
        return
    if resource_org_id is None or resource_org_id != p.org_id:
        raise not_found
    try:
        answers = await batch_decide(authz, p, [(min_visible, obj), (permission, obj)])
    except AuthzUnavailable as error:
        raise denials.authz_unavailable() from error
    if not answers[(min_visible, obj)]:
        raise not_found
    if not answers[(permission, obj)]:
        raise denials.permission_required(permission, request_access=True)


__all__ = [
    "RunDecisionMemo",
    "batch_decide",
    "fresh_run_decision_scope",
    "open_request_memo",
    "require_decided",
    "run_decision_scope",
]
