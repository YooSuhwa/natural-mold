"""Two real companies with tenant payloads and child/history records."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from schema_drafts.org_authz.default_org_backfill import TENANT
from schema_drafts.org_authz.schema import schema_metadata


@dataclass(frozen=True, slots=True)
class CompanyRows:
    rows: Mapping[str, UUID]


def seed_company(connection: Connection, metadata: sa.MetaData, *, existing: bool) -> CompanyRows:
    tenant, user, org = TENANT, uuid4(), uuid4()
    if existing:
        org = connection.scalar(sa.text("SELECT id FROM organizations LIMIT 1"))
    else:
        tenant = uuid4()
        connection.execute(
            sa.insert(metadata.tables["tenants"]).values(
                id=tenant, name="Other company", slug=tenant.hex, secrets_namespace=tenant.hex
            )
        )
        connection.execute(
            sa.insert(metadata.tables["organizations"]).values(
                id=org, tenant_id=tenant, name="Other organization", slug=org.hex
            )
        )
    connection.execute(
        sa.insert(metadata.tables["users"]).values(
            id=user, email=user.hex + "@rls.test", name="User"
        )
    )
    connection.execute(
        sa.insert(metadata.tables["tenant_members"]).values(tenant_id=tenant, user_id=user)
    )
    scope = {"tenant_id": tenant, "org_id": org}
    rows = {
        name: uuid4()
        for name in (
            "agents",
            "conversations",
            "credentials",
            "conversation_artifacts",
            "message_events",
            "message_event_chunks",
            "artifact_versions",
            "agent_deployments",
            "agent_api_threads",
            "agent_api_runs",
            "credential_audit_logs",
        )
    }
    rows["tenants"] = tenant
    rows["users"] = user
    connection.execute(
        sa.insert(metadata.tables["agents"]).values(
            id=rows["agents"],
            user_id=user,
            name="Agent",
            system_prompt="fixture",
            model_id=connection.scalar(sa.text("SELECT id FROM models LIMIT 1")),
            runtime_name="rls_" + rows["agents"].hex,
            **scope,
        )
    )
    connection.execute(
        sa.insert(metadata.tables["conversations"]).values(
            id=rows["conversations"],
            user_id=user,
            agent_id=rows["agents"],
            agent_name_snapshot="Agent",
            last_activity_at=datetime.now(UTC),
            **scope,
        )
    )
    connection.execute(
        sa.insert(metadata.tables["credentials"]).values(
            id=rows["credentials"],
            user_id=user,
            name="Credential",
            definition_key="http_bearer",
            data_encrypted="dummy-ciphertext",
            key_id="fixture",
            field_keys=[],
            **scope,
        )
    )
    connection.execute(
        sa.insert(metadata.tables["conversation_artifacts"]).values(
            id=rows["conversation_artifacts"],
            user_id=user,
            agent_id=rows["agents"],
            conversation_id=rows["conversations"],
            assistant_msg_id="artifact-turn",
            logical_path="/file.txt",
            display_name="File",
            mime_type="text/plain",
            size_bytes=1,
            sha256="a" * 64,
            **scope,
        )
    )
    connection.execute(
        sa.insert(metadata.tables["artifact_versions"]).values(
            id=rows["artifact_versions"],
            artifact_id=rows["conversation_artifacts"],
            version_number=1,
            object_key=tenant.hex + "/private.txt",
            original_filename="file.txt",
            size_bytes=1,
            sha256="a" * 64,
        )
    )
    connection.execute(
        sa.insert(metadata.tables["message_events"]).values(
            id=rows["message_events"],
            conversation_id=rows["conversations"],
            assistant_msg_id=rows["message_events"].hex,
            events=[{"private": tenant.hex}],
        )
    )
    connection.execute(
        sa.insert(metadata.tables["message_event_chunks"]).values(
            id=rows["message_event_chunks"],
            conversation_id=rows["conversations"],
            message_event_id=rows["message_events"],
            assistant_msg_id=rows["message_events"].hex,
            seq_start=0,
            seq_end=1,
            events=[{"private": tenant.hex}],
        )
    )
    connection.execute(
        sa.insert(metadata.tables["agent_deployments"]).values(
            id=rows["agent_deployments"],
            user_id=user,
            agent_id=rows["agents"],
            public_id=rows["agent_deployments"].hex,
            **scope,
        )
    )
    connection.execute(
        sa.insert(metadata.tables["agent_api_threads"]).values(
            id=rows["agent_api_threads"],
            user_id=user,
            deployment_id=rows["agent_deployments"],
            conversation_id=rows["conversations"],
            public_id=rows["agent_api_threads"].hex,
        )
    )
    connection.execute(
        sa.insert(metadata.tables["agent_api_runs"]).values(
            id=rows["agent_api_runs"],
            user_id=user,
            deployment_id=rows["agent_deployments"],
            conversation_id=rows["conversations"],
            thread_id=rows["agent_api_threads"],
            public_id=rows["agent_api_runs"].hex,
            mode="invoke",
            input={"private": tenant.hex},
        )
    )
    connection.execute(
        sa.insert(metadata.tables["credential_audit_logs"]).values(
            id=rows["credential_audit_logs"],
            credential_id=rows["credentials"],
            action="read",
            log_metadata={"private": tenant.hex},
        )
    )
    return CompanyRows(rows)


def two_companies(connection: Connection) -> tuple[sa.MetaData, CompanyRows, CompanyRows]:
    metadata = schema_metadata(later_columns=True)
    metadata.tables["conversations"].append_column(sa.Column("agent_name_snapshot", sa.String(100)))
    metadata.tables["conversations"].append_column(
        sa.Column("last_activity_at", sa.DateTime(timezone=True))
    )
    return (
        metadata,
        seed_company(connection, metadata, existing=True),
        seed_company(connection, metadata, existing=False),
    )
