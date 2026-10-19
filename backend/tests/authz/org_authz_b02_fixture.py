"""Real source-row fixtures for migration ownership precedence."""

from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa
from alembic.migration import MigrationContext as AlembicContext
from alembic.operations import Operations
from sqlalchemy.engine import Connection

from schema_drafts.org_authz import m78_tenants_organizations as m78
from schema_drafts.org_authz import m79_groups as m79
from schema_drafts.org_authz import m80_org_capabilities_invitations as m80
from schema_drafts.org_authz import m81_org_scope_columns as m81
from schema_drafts.org_authz.schema import baseline_metadata, schema_metadata

IDS = {
    name: uuid5(NAMESPACE_URL, f"moldy/b02/{name}")
    for name in (
        "admin",
        "api_user",
        "builder",
        "model",
        "agent",
        "hidden_agent",
        "deployment",
        "main",
        "api",
        "side",
        "hidden",
        "api_thread",
        "builder_session",
        "credential",
    )
}


def prepare(connection: Connection) -> sa.MetaData:
    baseline_metadata().create_all(connection)
    with Operations.context(AlembicContext.configure(connection)):
        for operation in (m78.upgrade, m79.upgrade, m80.upgrade, m81.upgrade):
            operation()
    return schema_metadata(scope_columns=True)


def seed(connection: Connection, metadata: sa.MetaData) -> dict[UUID, UUID]:
    admin, api_user, builder = (IDS[key] for key in ("admin", "api_user", "builder"))
    model, agent, hidden_agent, deployment = (
        IDS[key] for key in ("model", "agent", "hidden_agent", "deployment")
    )
    main, api, side, hidden = (IDS[key] for key in ("main", "api", "side", "hidden"))
    for index, user in enumerate((admin, api_user, builder)):
        connection.execute(
            sa.insert(metadata.tables["users"]).values(
                id=user,
                email=f"u{index}@schema.test",
                name=f"User {index}",
                is_super_user=index == 0,
            )
        )
    connection.execute(
        sa.insert(metadata.tables["models"]).values(
            id=model, provider="openai", model_name="fixture", display_name="Fixture"
        )
    )
    for key, profile in ((agent, "standard"), (hidden_agent, "skill_builder")):
        connection.execute(
            sa.insert(metadata.tables["agents"]).values(
                id=key,
                user_id=admin,
                name="Fixture",
                system_prompt="Fixture",
                runtime_name=f"fixture_{key.hex[:12]}",
                model_id=model,
                runtime_profile=profile,
            )
        )
    for key, source_agent, parent in (
        (main, agent, None),
        (api, agent, None),
        (side, agent, api),
        (hidden, hidden_agent, None),
    ):
        connection.execute(
            sa.insert(metadata.tables["conversations"]).values(
                id=key, agent_id=source_agent, side_chat_parent_id=parent
            )
        )
    connection.execute(
        sa.insert(metadata.tables["agent_deployments"]).values(
            id=deployment, agent_id=agent, user_id=admin, public_id="fixture-deployment"
        )
    )
    connection.execute(
        sa.insert(metadata.tables["agent_api_threads"]).values(
            id=IDS["api_thread"],
            public_id="fixture-thread",
            user_id=api_user,
            deployment_id=deployment,
            conversation_id=api,
        )
    )
    connection.execute(
        sa.insert(metadata.tables["skill_builder_sessions"]).values(
            id=IDS["builder_session"],
            user_id=builder,
            user_request="Fixture",
            conversation_id=hidden,
        )
    )
    connection.execute(
        sa.insert(metadata.tables["credentials"]).values(
            id=IDS["credential"],
            user_id=None,
            is_system=True,
            definition_key="http_bearer",
            name="Fixture",
            data_encrypted="fixture-ciphertext",
            key_id="fixture",
            field_keys=[],
        )
    )
    timestamp = datetime(2020, 1, 2, 3, 4, 5)
    for name in (
        "users",
        "agents",
        "conversations",
        "credentials",
        "skill_builder_sessions",
        "agent_deployments",
    ):
        table = metadata.tables[name]
        dates = {key: timestamp for key in ("created_at", "updated_at") if key in table.c}
        if dates:
            connection.execute(sa.update(table).values(**dates))
    return {main: admin, api: api_user, side: api_user, hidden: builder}
