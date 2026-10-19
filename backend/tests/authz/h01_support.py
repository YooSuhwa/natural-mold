"""Seeds and helpers for the H-01 access request tests (plan 9.7, 10.9, S18)."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy import select

from app.dependencies import CurrentUser, get_current_user
from app.models.access_request import AccessRequest
from app.models.audit_event import AuditEvent
from tests.conftest import TestSession

PATH = "/api/access-requests"


def body(kind: str, resource_id: object, level: str = "editor", **extra: object) -> dict:
    return {"resource_type": kind, "resource_id": str(resource_id), "level": level, **extra}


def tuples(user_id: UUID, *pairs: tuple[str, str]) -> frozenset[tuple[str, str, str]]:
    return frozenset((f"user:{user_id}", permission, obj) for permission, obj in pairs)


@contextmanager
def acting_as(app: FastAPI, user_id: UUID) -> Iterator[None]:
    actor = CurrentUser(id=user_id, email=f"{user_id}@t.io", name="Actor", is_super_user=False)

    async def override() -> CurrentUser:
        return actor

    previous = app.dependency_overrides[get_current_user]
    app.dependency_overrides[get_current_user] = override
    try:
        yield
    finally:
        app.dependency_overrides[get_current_user] = previous


async def requests() -> list[AccessRequest]:
    async with TestSession() as db:
        return list(await db.scalars(select(AccessRequest).order_by(AccessRequest.created_at)))


async def request_actions() -> list[str]:
    async with TestSession() as db:
        rows = await db.scalars(
            select(AuditEvent.action)
            .where(AuditEvent.target_type == "access_request")
            .order_by(AuditEvent.created_at)
        )
        return list(rows)
