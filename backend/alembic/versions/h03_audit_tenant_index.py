"""H-03 (plan 14.1, 6.2 m89 ``audit_events``): company/org audit list indexes.

Company (``/api/tenants/{id}/audit-events``) and organization (``scope=org``,
``/api/orgs/{id}/audit-events``) pages filter on ``tenant_id`` / ``org_id`` and page by
``(created_at, id)`` descending, so both indexes carry the full cursor key. The F-14
``ix_audit_events_org_created`` (org_id, created_at) is rebuilt with ``id`` appended.
"""

from __future__ import annotations

from alembic import op

revision = "h03_audit_tenant_index"
down_revision = "h01_access_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_audit_events_tenant_created", "audit_events", ["tenant_id", "created_at", "id"]
    )
    op.drop_index("ix_audit_events_org_created", table_name="audit_events")
    op.create_index("ix_audit_events_org_created", "audit_events", ["org_id", "created_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_org_created", table_name="audit_events")
    op.create_index("ix_audit_events_org_created", "audit_events", ["org_id", "created_at"])
    op.drop_index("ix_audit_events_tenant_created", table_name="audit_events")
