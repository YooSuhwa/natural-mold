"""Raw OpenFGA decisions behind the revocation deny overlay (plan 7.3, 8.2, 8.6).

``AuthzDecisions`` owns the client, the injected condition context and the D12
overlay: every OpenFGA allow from ``check``/``batch_check``/``list_objects`` is
vetoed while a pending revocation marker applies. These raw calls are for
enforce-mode code paths and the shadow recorder only; ``AuthzService``
(``service.py``) adds the mode-aware route-facing helpers.
"""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from uuid import UUID

from app.authz.client import CheckQuery, Consistency, ListQuery
from app.authz.principal import Principal
from app.authz.revocation_overlay import RevocationOverlay
from app.authz.service_types import (
    AuthzClient,
    AuthzMode,
    AuthzNotConsulted,
    ShadowDecision,
    ShadowHook,
    ShadowSample,
    Verdict,
)

if TYPE_CHECKING:
    from app.authz.outbox import TupleWriter

_Answers = dict[tuple[str, str], bool]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AuthzDecisions:
    def __init__(
        self,
        client: AuthzClient,
        *,
        mode: AuthzMode,
        enforce_types: frozenset[str] = frozenset(),
        shadow_hook: ShadowHook | None = None,
        clock: Callable[[], datetime] = _utc_now,
        overlay: RevocationOverlay | None = None,
    ) -> None:
        self.mode: AuthzMode = mode
        self.enforce_types = enforce_types
        self._client = client
        self._shadow_hook = shadow_hook
        self._clock = clock
        self._overlay = overlay

    def enforces(self, resource_type: str) -> bool:
        """An empty AUTHZ_ENFORCE_TYPES enforces every type."""
        return self.mode == "enforce" and (
            not self.enforce_types or resource_type in self.enforce_types
        )

    @property
    def tuple_writer(self) -> "TupleWriter | None":
        """The client as an outbox writer for post-commit apply; ``None`` in legacy mode."""
        client = self._client
        if self.mode == "legacy" or not (hasattr(client, "write") and hasattr(client, "delete")):
            return None
        return cast("TupleWriter", client)

    def condition_context(self, p: Principal) -> dict[str, int | str]:
        """Injected into every query so conditional tuples are always evaluable."""
        now = self._clock().astimezone(UTC).replace(microsecond=0, tzinfo=None)
        return {
            "current_time": f"{now.isoformat()}Z",
            "run_source": p.run_source,
            "user_ip": p.user_ip or "0.0.0.0",  # noqa: S104 - plan 7.3 sentinel, not a bind
        }

    def _consulted(self) -> AuthzClient:
        if self.mode == "legacy":
            raise AuthzNotConsulted("AUTHZ_MODE=legacy does not consult OpenFGA")
        return self._client

    def _query(self, p: Principal, permission: str, obj: str) -> CheckQuery:
        return CheckQuery(
            user=p.fga(), relation=permission, object=obj, context=self.condition_context(p)
        )

    async def check(
        self,
        p: Principal,
        permission: str,
        obj: str,
        *,
        consistency: Consistency = "MINIMIZE_LATENCY",
    ) -> bool:
        query = self._query(p, permission, obj)
        allowed = await self._consulted().check(query, consistency=consistency)
        return allowed and not await self._revoked(p, [obj])

    async def batch_check(
        self,
        p: Principal,
        items: Sequence[tuple[str, str]],
        *,
        consistency: Consistency = "MINIMIZE_LATENCY",
    ) -> dict[tuple[str, str], bool]:
        """Duplicates are merged; the client splits into 50-item correlated batches."""
        return await self._vetoed(p, await self._fga_batch(p, items, consistency))

    async def _fga_batch(
        self,
        p: Principal,
        items: Sequence[tuple[str, str]],
        consistency: Consistency = "MINIMIZE_LATENCY",
    ) -> _Answers:
        unique = list(dict.fromkeys(items))
        queries = [self._query(p, permission, obj) for permission, obj in unique]
        answers = await self._consulted().batch_check(queries, consistency=consistency)
        return dict(zip(unique, answers, strict=True))

    async def _vetoed(self, p: Principal, answers: _Answers) -> _Answers:
        revoked = await self._revoked(p, [obj for (_, obj), ok in answers.items() if ok])
        return {item: ok and item[1] not in revoked for item, ok in answers.items()}

    async def list_objects(self, p: Principal, permission: str, type_: str) -> set[str]:
        """Small sets only; pages use candidates + filter_visible (plan 8.10)."""
        query = ListQuery(
            user=p.fga(), relation=permission, type=type_, context=self.condition_context(p)
        )
        found = set(await self._consulted().list_objects(query))
        return found - await self._revoked(p, sorted(found))

    async def _revoked(self, p: Principal, allowed: list[str]) -> frozenset[str]:
        """Allowed objects vetoed by a pending revocation marker (fails closed)."""
        if self._overlay is None or not allowed:
            return frozenset()
        return await self._overlay.blocked(p, allowed)

    async def evaluate(
        self,
        p: Principal,
        permission: str,
        obj: str,
        *,
        min_visible: str,
        resource_org_id: UUID | None,
    ) -> Verdict:
        """OpenFGA verdict per 8.4; missing/foreign resources are decided before any call."""
        if resource_org_id is None or resource_org_id != p.org_id:
            return Verdict.NOT_FOUND
        answers = await self.batch_check(p, [(min_visible, obj), (permission, obj)])
        if not answers[(min_visible, obj)]:
            return Verdict.NOT_FOUND
        return Verdict.ALLOW if answers[(permission, obj)] else Verdict.FORBIDDEN

    async def shadow_decide(self, sample: ShadowSample) -> ShadowDecision:
        """C-06 recorder: the overlaid OpenFGA verdict, flagging revocation-only vetoes."""
        p, obj, visible = sample.principal, sample.obj, sample.min_visible
        if sample.resource_org_id != p.org_id:  # missing (None) or foreign
            return ShadowDecision(Verdict.NOT_FOUND)
        items = [(visible, obj)] if visible else []
        raw = await self._fga_batch(p, [*items, (sample.permission, obj)])
        final = await self._vetoed(p, raw)

        def verdict(answers: _Answers) -> Verdict:
            if visible and not answers[(visible, obj)]:
                return Verdict.NOT_FOUND
            return Verdict.ALLOW if answers[(sample.permission, obj)] else Verdict.FORBIDDEN

        decided = verdict(final)
        return ShadowDecision(decided, overlay_vetoed=decided is not verdict(raw))
