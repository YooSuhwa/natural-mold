"""Transaction-owned orchestration for the immutable default-organization backfill."""

from sqlalchemy.engine import Connection

from app.migrations.org_authz_v1 import provenance, snapshot_io
from app.migrations.org_authz_v1.backfill_rows import (
    backfill_scopes,
    insert_if_missing,
    seed_memberships,
)
from app.migrations.org_authz_v1.contracts import ORG, TENANT
from app.migrations.org_authz_v1.conversation_owners import resolve_conversations
from app.migrations.org_authz_v1.metadata import migration_context
from app.migrations.org_authz_v1.settings import MigrationSettings


def backfill(connection: Connection, settings: MigrationSettings | None = None) -> None:
    config = settings if settings is not None else MigrationSettings.from_environ()
    provenance.lock_writers(connection)
    context = migration_context(connection)
    existing = snapshot_io.load(context)
    if existing is not None:
        provenance.verify(context, existing)
        return
    provenance.default_references(context, set())
    before = provenance.capture(context)
    insert_if_missing(
        context,
        context.metadata.tables["tenants"],
        {
            "id": TENANT,
            "name": config.default_tenant_name,
            "slug": "moldy-default",
            "limits": config.tenant_default_limits,
            "settings": {},
            "secrets_namespace": f"tenants/{TENANT}",
            "data_keys": {},
        },
    )
    insert_if_missing(
        context,
        context.metadata.tables["organizations"],
        {
            "id": ORG,
            "tenant_id": TENANT,
            "name": config.default_org_name,
            "slug": "default",
            "is_default": True,
            "settings": {"default_member_role": "member"},
        },
    )
    seed_memberships(context)
    resolve_conversations(context)
    backfill_scopes(context)
    snapshot_io.save(context, before, provenance.capture(context))


def reverse_backfill(connection: Connection) -> None:
    provenance.lock_writers(connection)
    context = migration_context(connection)
    snapshot = snapshot_io.load(context)
    if snapshot is None:
        message = "m82 migration snapshot unavailable; rollback requires reconciliation"
        raise ValueError(message)
    provenance.verify(context, snapshot)
    provenance.restore(context, snapshot)
