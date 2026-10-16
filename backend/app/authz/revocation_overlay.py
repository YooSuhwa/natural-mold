"""Deny overlay: pending revocation markers veto stale OpenFGA allows (plan D12, 8.6).

``AuthzService`` asks the overlay only about objects OpenFGA allowed. The
principal's revocation subjects are its own FGA subject plus, for an agent
acting for a user, ``user:<acting_user_id>``. A marker blocks a decision of
principal ``p`` on ``obj`` in ``p.org_id`` when it is unfinalized and either

* names one of those subjects with an organization-wide scope (membership,
  group member, role, capability) — every decision in that organization is
  refused, or
* names ``obj`` and either one of those subjects or a non-actor subject (a
  group, the organization, ``*``) — a revoked group share or a deleted resource
  refuses the object for everyone, owners included. Such a marker stays in
  force until C-04 finalizes it; a stuck marker is surfaced through
  ``revocations.count_pending``/``oldest_pending_at`` for C-04/C-05 alerting
  rather than being exempted here.

Markers of other organizations never apply. A storage failure or timeout raises
``AuthzUnavailable`` so it can never turn into an allow.
"""

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.client import AuthzUnavailable
from app.authz.pending_markers import PendingMarkers, pending_markers
from app.authz.principal import Principal
from app.authz.revocations import ORG_WIDE_SCOPES, count_pending
from app.models.authz import AuthzRevocation

_ACTOR_PREFIXES = ("user:", "agent:")
_STORAGE_FAILURES = (SQLAlchemyError, OSError, TimeoutError)


class RevocationOverlay(Protocol):
    async def blocked(self, p: Principal, objs: Sequence[str]) -> frozenset[str]:
        """The subset of ``objs`` (all already allowed by OpenFGA) to refuse."""
        ...


def blocked_objects(
    rows: Sequence[tuple[str, str, str]], p: Principal, objs: Sequence[str]
) -> frozenset[str]:
    """Pure matching of pending ``(scope, subject, object)`` rows for one principal."""
    subjects, wanted = p.revocation_subjects(), frozenset(objs)
    blocked: set[str] = set()
    for scope, marker_subject, marker_object in rows:
        if marker_subject in subjects and scope in ORG_WIDE_SCOPES:
            return wanted
        if marker_object in wanted and (
            marker_subject in subjects or not marker_subject.startswith(_ACTOR_PREFIXES)
        ):
            blocked.add(marker_object)
    return frozenset(blocked)


class DbRevocationOverlay:
    """Reads committed markers through a platform-scoped session factory.

    ``platform_sessions`` must not be company-scoped: the 1-second pending hint
    counts markers instance-wide (8.6 step 4). The per-decision query filters
    by ``p.org_id`` explicitly, so it never reads another organization's markers.
    """

    def __init__(
        self,
        platform_sessions: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        *,
        hint: PendingMarkers = pending_markers,
    ) -> None:
        self._sessions = platform_sessions
        self._hint = hint

    async def _count(self) -> int:
        async with self._sessions() as db:
            return await count_pending(db)

    async def blocked(self, p: Principal, objs: Sequence[str]) -> frozenset[str]:
        if not objs:
            return frozenset()
        try:
            if not await self._hint.any_pending(self._count):
                return frozenset()
            async with self._sessions() as db:
                rows = await db.execute(
                    select(
                        AuthzRevocation.scope, AuthzRevocation.subject, AuthzRevocation.object
                    ).where(
                        AuthzRevocation.org_id == p.org_id,
                        AuthzRevocation.finalized_at.is_(None),
                        or_(
                            AuthzRevocation.subject.in_(sorted(p.revocation_subjects())),
                            AuthzRevocation.object.in_(sorted(set(objs))),
                        ),
                    )
                )
                found = [(row[0], row[1], row[2]) for row in rows.all()]
        except _STORAGE_FAILURES as error:
            raise AuthzUnavailable("revocation_overlay") from error
        return blocked_objects(found, p, objs)
