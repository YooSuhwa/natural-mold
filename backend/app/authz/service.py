"""Mode-aware OpenFGA decisions with injected condition context (plan 7.3, 8.2-8.5).

``legacy`` never consults OpenFGA; ``shadow`` answers with the caller's legacy
answer and hands samples to the shadow recorder (C-06); ``enforce`` answers with
OpenFGA for enforced types and fails closed (503) when it is unavailable.
Route-facing calls (``require``, ``require_cap``, ``filter_visible``,
``my_permissions``, ``can_request_access``) take the legacy answer and apply the
mode themselves. The raw ``check``/``batch_check``/``list_objects`` calls are for
enforce-mode code paths and the shadow recorder (via ``evaluate``) only.
Capability checks are enforced only when ``organization`` is an enforced type.
"""

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from uuid import UUID

from app.authz import denials
from app.authz.client import AuthzUnavailable, CheckQuery, Consistency, ListQuery
from app.authz.config import AuthzSettings
from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.service_types import (
    AuthzClient,
    AuthzMode,
    AuthzNotConsulted,
    ShadowHook,
    ShadowSample,
    Verdict,
    known_resource_types,
    parse_enforce_types,
)
from app.exceptions import AppError

__all__ = [
    "AuthzClient",
    "AuthzMode",
    "AuthzNotConsulted",
    "AuthzService",
    "ShadowHook",
    "ShadowSample",
    "Verdict",
    "known_resource_types",
]
logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AuthzService:
    def __init__(
        self,
        client: AuthzClient,
        *,
        mode: AuthzMode,
        enforce_types: frozenset[str] = frozenset(),
        shadow_hook: ShadowHook | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.mode = mode
        self.enforce_types = enforce_types
        self._client = client
        self._shadow_hook = shadow_hook
        self._clock = clock

    @classmethod
    def from_settings(
        cls, client: AuthzClient, settings: AuthzSettings, *, shadow_hook: ShadowHook | None = None
    ) -> "AuthzService":
        types = parse_enforce_types(settings.authz_enforce_types)
        return cls(client, mode=settings.authz_mode, enforce_types=types, shadow_hook=shadow_hook)

    def enforces(self, resource_type: str) -> bool:
        """An empty AUTHZ_ENFORCE_TYPES enforces every type."""
        return self.mode == "enforce" and (
            not self.enforce_types or resource_type in self.enforce_types
        )

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
        return await self._consulted().check(query, consistency=consistency)

    async def batch_check(
        self, p: Principal, items: Sequence[tuple[str, str]]
    ) -> dict[tuple[str, str], bool]:
        """Duplicates are merged; the client splits into 50-item correlated batches."""
        unique = list(dict.fromkeys(items))
        queries = [self._query(p, permission, obj) for permission, obj in unique]
        answers = await self._consulted().batch_check(queries)
        return dict(zip(unique, answers, strict=True))

    async def list_objects(self, p: Principal, permission: str, type_: str) -> set[str]:
        """Small sets only; pages use candidates + filter_visible (plan 8.10)."""
        query = ListQuery(
            user=p.fga(), relation=permission, type=type_, context=self.condition_context(p)
        )
        return set(await self._consulted().list_objects(query))

    async def filter_visible(
        self, p: Principal, permission: str, objs: list[str], *, legacy: list[str]
    ) -> list[str]:
        """Confirm an org-scoped candidate page; ``legacy`` is the existing visible subset."""
        if not objs or not all(self.enforces(obj.partition(":")[0]) for obj in objs):
            kept = set(legacy)
            for obj in objs:
                verdict = Verdict.ALLOW if obj in kept else Verdict.NOT_FOUND
                self._shadow(ShadowSample(p, permission, obj, permission, p.org_id, verdict))
            return [obj for obj in objs if obj in kept]
        answers = await self._enforced_batch(p, [(permission, obj) for obj in objs])
        return [obj for obj in objs if answers[(permission, obj)]]

    async def my_permissions(
        self, p: Principal, obj: str, perms: list[str], *, legacy: list[str]
    ) -> list[str]:
        """The response's ``my_permissions``; ``legacy`` is the existing answer."""
        if not self.enforces(obj.partition(":")[0]):
            held = set(legacy)
            for perm in perms:
                verdict = Verdict.ALLOW if perm in held else Verdict.FORBIDDEN
                self._shadow(ShadowSample(p, perm, obj, None, p.org_id, verdict))
            return [perm for perm in perms if perm in held]
        answers = await self._enforced_batch(p, [(perm, obj) for perm in perms])
        return [perm for perm in perms if answers[(perm, obj)]]

    async def _enforced_batch(
        self, p: Principal, items: list[tuple[str, str]]
    ) -> dict[tuple[str, str], bool]:
        try:
            return await self.batch_check(p, items)
        except AuthzUnavailable as error:
            raise denials.authz_unavailable() from error

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

    async def require(
        self,
        p: Principal,
        permission: str,
        obj: str,
        *,
        min_visible: str,
        resource_org_id: UUID | None,
        legacy: Verdict,
        not_found: AppError | None = None,
        forbidden: AppError | None = None,
        request_access: bool = True,
    ) -> None:
        """Raise the 8.4 response for ``permission`` on ``obj``; return when allowed.

        ``not_found`` keeps a route's existing 404 in every mode; ``forbidden`` keeps
        its existing 403 only while the type is not enforced (8.4 shape otherwise).
        """
        enforced = self.enforces(obj.partition(":")[0])
        if enforced:
            try:
                verdict = await self.evaluate(
                    p, permission, obj, min_visible=min_visible, resource_org_id=resource_org_id
                )
            except AuthzUnavailable as error:
                raise denials.authz_unavailable() from error
        else:
            verdict = legacy
            self._shadow(ShadowSample(p, permission, obj, min_visible, resource_org_id, legacy))
        if verdict is Verdict.NOT_FOUND:
            raise not_found or denials.not_found()
        if verdict is Verdict.FORBIDDEN:
            if forbidden is not None and not enforced:
                raise forbidden
            raise denials.permission_required(permission, request_access=request_access)

    async def require_cap(self, ctx: OrgContext, cap: str, *, legacy: bool) -> None:
        """Organization capability (``cap_*``); missing → 403 capability_required.

        Enforced only when ``organization`` is an enforced type (or the list is empty).
        """
        p = Principal.for_user(ctx)
        obj = f"organization:{ctx.org_id}"
        if self.enforces("organization"):
            try:
                allowed = await self.check(p, cap, obj)
            except AuthzUnavailable as error:
                raise denials.authz_unavailable() from error
        else:
            allowed = legacy
            verdict = Verdict.ALLOW if legacy else Verdict.FORBIDDEN
            self._shadow(ShadowSample(p, cap, obj, None, ctx.org_id, verdict))
        if not allowed:
            raise denials.capability_required(cap)

    async def can_request_access(
        self,
        p: Principal,
        obj: str,
        *,
        min_visible: str,
        resource_org_id: UUID | None,
        legacy_visible: bool,
    ) -> bool:
        """Whether a real access request may be created; the response is always 202."""
        if not self.enforces(obj.partition(":")[0]):
            return legacy_visible
        try:
            verdict = await self.evaluate(
                p, min_visible, obj, min_visible=min_visible, resource_org_id=resource_org_id
            )
        except AuthzUnavailable:
            return False  # fail closed; the 202 body reveals nothing either way
        return verdict is Verdict.ALLOW

    def _shadow(self, sample: ShadowSample) -> None:
        """The recorder must never change or delay the legacy response."""
        if self.mode == "legacy" or self._shadow_hook is None:
            return
        try:
            self._shadow_hook(sample)
        except Exception:
            logger.warning("authz shadow hook failed", exc_info=True)
