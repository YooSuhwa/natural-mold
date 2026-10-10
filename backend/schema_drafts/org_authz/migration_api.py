"""Shared exact table definitions used by staged Alembic drafts."""

from alembic import op
from schema_drafts.org_authz.schema import schema_metadata


def create_tables(*names: str) -> None:
    metadata = schema_metadata()
    for name in names:
        metadata.tables[name].create(op.get_bind())


def drop_tables(*names: str) -> None:
    metadata = schema_metadata()
    for name in reversed(names):
        metadata.tables[name].drop(op.get_bind())
