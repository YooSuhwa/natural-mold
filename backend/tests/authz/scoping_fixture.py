"""Real SQL fixture independent of the application's pending ORM scope promotion."""

from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID

import pytest
from sqlalchemy import ForeignKey, create_engine, insert
from sqlalchemy.engine import Connection
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.authz.org_context import OrgContext


class ScopeBase(DeclarativeBase):
    pass


class ScopedResource(ScopeBase):
    __tablename__ = "scoped_resources"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID | None] = mapped_column()
    org_id: Mapped[UUID | None] = mapped_column()
    user_id: Mapped[UUID | None] = mapped_column()


class ScopedChild(ScopeBase):
    __tablename__ = "scoped_children"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("scoped_resources.id"))


class RequiredScopedResource(ScopeBase):
    __tablename__ = "required_scoped_resources"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column()
    org_id: Mapped[UUID] = mapped_column()
    user_id: Mapped[UUID] = mapped_column()


@dataclass(frozen=True, slots=True)
class ScopeFixture:
    connection: Connection
    context: OrgContext
    second: OrgContext


@pytest.fixture
def scope_fixture() -> Iterator[ScopeFixture]:
    first = OrgContext(org_id=UUID(int=1), tenant_id=UUID(int=10), user_id=UUID(int=100))
    second = OrgContext(org_id=UUID(int=2), tenant_id=UUID(int=20), user_id=first.user_id)
    engine = create_engine("sqlite://")
    try:
        ScopeBase.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(
                insert(ScopedResource),
                [
                    {
                        "id": 1,
                        "org_id": first.org_id,
                        "tenant_id": first.tenant_id,
                        "user_id": first.user_id,
                    },
                    {
                        "id": 2,
                        "org_id": second.org_id,
                        "tenant_id": second.tenant_id,
                        "user_id": first.user_id,
                    },
                    {
                        "id": 3,
                        "org_id": first.org_id,
                        "tenant_id": first.tenant_id,
                        "user_id": UUID(int=200),
                    },
                    {
                        "id": 4,
                        "org_id": first.org_id,
                        "tenant_id": second.tenant_id,
                        "user_id": first.user_id,
                    },
                    {"id": 5, "org_id": None, "tenant_id": None, "user_id": None},
                ],
            )
            connection.execute(
                insert(ScopedChild), [{"id": number, "parent_id": number} for number in range(1, 6)]
            )
            connection.execute(
                insert(RequiredScopedResource),
                [
                    {
                        "id": 1,
                        "org_id": first.org_id,
                        "tenant_id": first.tenant_id,
                        "user_id": first.user_id,
                    }
                ],
            )
            yield ScopeFixture(connection=connection, context=first, second=second)
    finally:
        engine.dispose()
