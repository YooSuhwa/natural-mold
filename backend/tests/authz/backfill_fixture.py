"""Real source-row fixtures for migration ownership precedence."""

from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Connection


def seed(connection: Connection, metadata: sa.MetaData) -> dict[UUID, UUID]:
    admin, api_user, builder = uuid4(), uuid4(), uuid4()
    model, agent, hidden_agent, deployment = uuid4(), uuid4(), uuid4(), uuid4()
    main, api, side, hidden = uuid4(), uuid4(), uuid4(), uuid4()
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
            public_id="fixture-thread",
            user_id=api_user,
            deployment_id=deployment,
            conversation_id=api,
        )
    )
    connection.execute(
        sa.insert(metadata.tables["skill_builder_sessions"]).values(
            user_id=builder, user_request="Fixture", conversation_id=hidden
        )
    )
    connection.execute(
        sa.insert(metadata.tables["credentials"]).values(
            user_id=None,
            is_system=True,
            definition_key="http_bearer",
            name="Fixture",
            data_encrypted="fixture-ciphertext",
            key_id="fixture",
            field_keys=[],
        )
    )
    return {main: admin, api: api_user, side: api_user, hidden: builder}
