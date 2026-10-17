"""Per-link verdicts of the delegation validator (plan 10.6, 8.6, 8.4 일관성 옵션; F-03).

A link is judged only on settled state:

* deferred when a pending revocation marker covers its delegator or objects (the marker
  vetoes every allow until OpenFGA confirmed the delete — breaking then would turn a
  narrower-for-seconds decision into a persistent break);
* deferred, when it would be lost, while an undelivered outbox row touches its objects
  or names its delegator (a share/membership write not yet in OpenFGA could still keep
  the access);
* otherwise decided with ``HIGHER_CONSISTENCY`` checks (8.6: decisions right after a
  permission change). ``AuthzUnavailable`` propagates: nothing is judged.
"""

from collections import defaultdict
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.delegation_links import DelegatedLink
from app.authz.principal import Principal
from app.authz.revocation_overlay import blocked_objects
from app.authz.service_core import AuthzDecisions
from app.models.authz import AuthzOutbox, AuthzRevocation

Reason = Literal["delegator_lost_access", "resource_unavailable"]
Verdict = Literal["ok", "lost", "deferred"]
Decision = tuple[Verdict, Reason]


async def decide(
    db: AsyncSession, authz: AuthzDecisions, org_id: UUID, links: list[DelegatedLink]
) -> dict[DelegatedLink, Decision]:
    """Per link: OK, lost (with reason) or deferred (module doc)."""
    pending = [
        (row[0], row[1], row[2])
        for row in (
            await db.execute(
                select(AuthzRevocation.scope, AuthzRevocation.subject, AuthzRevocation.object)
                .where(AuthzRevocation.org_id == org_id, AuthzRevocation.finalized_at.is_(None))
                .order_by(AuthzRevocation.id)
            )
        ).all()
    ]
    found: dict[DelegatedLink, Decision] = {}
    asked: dict[UUID, list[DelegatedLink]] = defaultdict(list)
    for link in links:
        if not link.grantable:
            found[link] = ("lost", "resource_unavailable")
        elif link.delegator is None:  # the delegator's account was deleted (SET NULL)
            found[link] = ("lost", "delegator_lost_access")
        elif blocked_objects(pending, principal(link.delegator, org_id), objects(link)):
            found[link] = ("deferred", "delegator_lost_access")
        else:
            asked[link.delegator].append(link)
    for delegator in sorted(asked, key=lambda value: value.bytes):
        group = asked[delegator]
        checks = list(dict.fromkeys(check for link in group for check in link.checks))
        answers = await authz.batch_check(
            principal(delegator, org_id), checks, consistency="HIGHER_CONSISTENCY"
        )
        for link in group:
            ok = all(answers[check] for check in link.checks)
            found[link] = ("ok" if ok else "lost", "delegator_lost_access")
    return await _hold_undelivered(db, found)


async def _hold_undelivered(
    db: AsyncSession, found: dict[DelegatedLink, Decision]
) -> dict[DelegatedLink, Decision]:
    lost = [link for link, (verdict, _) in found.items() if verdict == "lost"]
    if not lost:
        return found
    keys = await db.scalars(select(AuthzOutbox.tuple_key).where(AuthzOutbox.processed_at.is_(None)))
    users, objs = set(), set()
    for key in keys:
        user, _, obj = key.split(" ", 2) if key.count(" ") >= 2 else ("", "", "")
        users.add(user.partition("#")[0])
        objs.add(obj)
    for link in lost:
        if f"user:{link.delegator}" in users or objs.intersection(objects(link)):
            found[link] = ("deferred", found[link][1])
    return found


def principal(user_id: UUID, org_id: UUID) -> Principal:
    return Principal(kind="user", id=user_id, org_id=org_id)


def objects(link: DelegatedLink) -> list[str]:
    return sorted({f"{link.obj[0]}:{link.obj[1]}", *(obj for _, obj in link.checks)})
