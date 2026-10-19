"""Notification contracts (plan 9.7 알림, 14.2, S25; H-02)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class NotificationTarget(BaseModel):
    """``accessible=False``: the recipient lost access; id and name are withheld (14.2)."""

    type: str
    id: UUID | None = None
    name: str | None = None
    accessible: bool = True


class NotificationActor(BaseModel):
    id: UUID
    display_name: str | None = None


class NotificationView(BaseModel):
    id: UUID
    kind: str
    org_id: UUID | None
    target: NotificationTarget | None
    actor: NotificationActor | None
    payload: dict[str, str | int | bool | None]
    link: str | None
    read_at: datetime | None
    created_at: datetime


class NotificationPage(BaseModel):
    items: list[NotificationView]
    next_cursor: str | None = None
    has_more: bool = False


class UnreadCount(BaseModel):
    total: int
    by_org: dict[str, int]


class ReadAllResult(BaseModel):
    updated: int
