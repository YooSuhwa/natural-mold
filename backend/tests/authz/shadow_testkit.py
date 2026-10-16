"""Shared helpers for the C-06 shadow recorder tests."""

from sqlalchemy import select

from app.authz.service import ShadowDecision, ShadowSample, Verdict
from app.authz.shadow import ShadowRecorder
from app.authz.shadow_store import diff_sessions
from app.models.authz import AuthzShadowDiff
from tests.authz.authz_service_testkit import AGENT, ORG, user
from tests.conftest import TestSession


def sample(legacy: Verdict = Verdict.ALLOW, *, permission: str = "edit") -> ShadowSample:
    return ShadowSample(user(), permission, AGENT, "view", ORG, legacy)


def answering(verdict: Verdict, *, vetoed: bool = False, calls: list | None = None):
    async def decide(s: ShadowSample) -> ShadowDecision:
        if calls is not None:
            calls.append(s)
        return ShadowDecision(verdict, overlay_vetoed=vetoed)

    return decide


def recorder(decide, **kw) -> ShadowRecorder:
    kw.setdefault("max_rps", 1000)
    return ShadowRecorder(decide, sessions=diff_sessions(TestSession), **kw)


async def rows() -> list[AuthzShadowDiff]:
    async with TestSession() as db:
        return list((await db.execute(select(AuthzShadowDiff))).scalars())
