"""Native retained-graph constraints for the deployable B08 migration."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.authz.agent_lifecycle_fixture import registered_lifecycle_fixture

pytestmark = pytest.mark.integration


async def test_native_constraints_and_retention_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a real migrated PostgreSQL graph with every retained Agent reference.
    async with registered_lifecycle_fixture(monkeypatch) as fixture:
        graph = fixture.graph
        async with fixture.sessions() as db:
            foreign_keys = await db.execute(
                text(
                    "SELECT tc.table_name, kcu.column_name, rc.delete_rule, "
                    "columns.is_nullable FROM information_schema.table_constraints tc "
                    "JOIN information_schema.key_column_usage kcu "
                    "ON tc.constraint_name=kcu.constraint_name "
                    "AND tc.constraint_schema=kcu.constraint_schema "
                    "JOIN information_schema.referential_constraints rc "
                    "ON tc.constraint_name=rc.constraint_name "
                    "AND tc.constraint_schema=rc.constraint_schema "
                    "JOIN information_schema.columns columns "
                    "ON columns.table_schema=tc.table_schema "
                    "AND columns.table_name=tc.table_name "
                    "AND columns.column_name=kcu.column_name "
                    "WHERE tc.constraint_type='FOREIGN KEY'"
                )
            )
            fk_contract = {
                (str(table), str(column)): (str(rule), str(nullable))
                for table, column, rule, nullable in foreign_keys.tuples()
            }

            # When invalid lifecycle, participant, binding, receipt and snapshot states are tried.
            with pytest.raises(IntegrityError, match="ck_conversations_agent_lifecycle_binding"):
                async with db.begin_nested():
                    await db.execute(
                        text("UPDATE conversations SET agent_id=NULL WHERE id=:conversation_id"),
                        {"conversation_id": graph.conversation_ids[0]},
                    )
            with pytest.raises(IntegrityError, match="ck_conversations_agent_lifecycle_binding"):
                async with db.begin_nested():
                    await db.execute(
                        text(
                            "UPDATE conversations SET agent_lifecycle_state='purged', "
                            "agent_purged_at=now() WHERE id=:conversation_id"
                        ),
                        {"conversation_id": graph.conversation_ids[0]},
                    )
            with pytest.raises(IntegrityError, match="uq_conversation_run_agents_snapshot"):
                async with db.begin_nested():
                    await db.execute(
                        text(
                            "INSERT INTO conversation_run_agents "
                            "(id,run_id,agent_id,agent_id_snapshot,role,path,created_at) "
                            "SELECT :id,run_id,agent_id,agent_id_snapshot,role,path,now() "
                            "FROM conversation_run_agents WHERE run_id=:run_id LIMIT 1"
                        ),
                        {"id": uuid4(), "run_id": graph.run_ids[0]},
                    )
            with pytest.raises(IntegrityError, match="ck_conversation_run_inputs_binding"):
                async with db.begin_nested():
                    await db.execute(
                        text("UPDATE conversation_run_inputs SET run_id=NULL WHERE id=:input_id"),
                        {"input_id": graph.input_ids[0]},
                    )
            with pytest.raises(IntegrityError, match="ck_agent_purge_receipts_state"):
                async with db.begin_nested():
                    await db.execute(
                        text(
                            "INSERT INTO agent_purge_receipts "
                            "(id,tenant_id,org_id,agent_id_snapshot,owner_user_id_snapshot,"
                            "agent_name_snapshot,state,created_at,updated_at) VALUES "
                            "(:id,:tenant,:org,:agent,:owner,'fixture','invalid',now(),now())"
                        ),
                        {
                            "id": uuid4(),
                            "tenant": graph.tenant_id,
                            "org": graph.org_id,
                            "agent": graph.root_agent_id,
                            "owner": graph.root_owner_id,
                        },
                    )
            with pytest.raises(IntegrityError, match="ck_conversation_history_manifests_state"):
                async with db.begin_nested():
                    await db.execute(
                        text(
                            "INSERT INTO conversation_history_manifests "
                            "(id,conversation_id,schema_version,source_agent_id_snapshot,state,"
                            "checkpoint_count,event_count,canonical_payload_sha256,created_at,"
                            "updated_at) VALUES "
                            "(:id,:conversation,1,:agent,'invalid',0,0,:checksum,now(),now())"
                        ),
                        {
                            "id": uuid4(),
                            "conversation": graph.conversation_ids[0],
                            "agent": graph.root_agent_id,
                            "checksum": "0" * 64,
                        },
                    )

            # Then the catalog keeps retained references nullable SET NULL and roots CASCADE.
            for table_name in (
                "conversation_artifacts",
                "conversation_run_inputs",
                "conversation_runs",
                "mcp_app_invocation_bindings",
                "token_usages",
            ):
                assert fk_contract[(table_name, "agent_id")] == ("SET NULL", "YES")
            for table_name in (
                "conversation_artifacts",
                "conversation_run_inputs",
                "conversation_runs",
                "mcp_app_invocation_bindings",
                "token_usages",
            ):
                assert fk_contract[(table_name, "conversation_id")] == ("CASCADE", "NO")

            # When the fixture Agent is physically removed after its Conversations are detached.
            await db.execute(
                text(
                    "UPDATE conversations SET agent_id=NULL, agent_lifecycle_state='purged', "
                    "agent_purged_at=now() WHERE agent_id=:agent_id"
                ),
                {"agent_id": graph.root_agent_id},
            )
            await db.execute(
                text("DELETE FROM agents WHERE id=:agent_id"),
                {"agent_id": graph.root_agent_id},
            )
            await db.commit()

            # Then history remains detached while operational deployment/trigger rows are gone.
            retained_nulls = await db.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM conversation_runs "
                    " WHERE id = ANY(:run_ids) AND agent_id IS NULL),"
                    "(SELECT count(*) FROM conversation_run_inputs "
                    " WHERE id = ANY(:input_ids) AND agent_id IS NULL),"
                    "(SELECT count(*) FROM conversation_artifacts "
                    " WHERE id=:artifact_id AND agent_id IS NULL),"
                    "(SELECT count(*) FROM token_usages "
                    " WHERE conversation_id = ANY(:conversation_ids) AND agent_id IS NULL),"
                    "(SELECT count(*) FROM mcp_app_invocation_bindings "
                    " WHERE id=:binding_id AND agent_id IS NULL),"
                    "(SELECT count(*) FROM agent_api_runs "
                    " WHERE id=:api_run_id AND deployment_id IS NULL),"
                    "(SELECT count(*) FROM agent_trigger_runs WHERE id=:trigger_run_id "
                    " AND trigger_id IS NULL AND agent_id IS NULL),"
                    "(SELECT count(*) FROM agent_deployments WHERE agent_id=:agent_id),"
                    "(SELECT count(*) FROM agent_triggers WHERE agent_id=:agent_id)"
                ),
                {
                    "agent_id": graph.root_agent_id,
                    "api_run_id": graph.api_run_id,
                    "artifact_id": graph.artifact_id,
                    "binding_id": graph.mcp_binding_id,
                    "conversation_ids": list(graph.conversation_ids),
                    "input_ids": list(graph.input_ids),
                    "run_ids": list(graph.run_ids),
                    "trigger_run_id": graph.trigger_run_id,
                },
            )
            assert retained_nulls.one() == (3, 3, 1, 1, 1, 1, 1, 0, 0)

            # When the Conversation retention root is deleted.
            await db.execute(
                text("DELETE FROM conversations WHERE id=:conversation_id"),
                {"conversation_id": graph.conversation_ids[0]},
            )
            await db.commit()

            # Then its descendants are removed by Conversation CASCADE.
            descendants = await db.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM conversation_runs WHERE conversation_id=:id),"
                    "(SELECT count(*) FROM conversation_run_inputs WHERE conversation_id=:id),"
                    "(SELECT count(*) FROM conversation_artifacts WHERE conversation_id=:id),"
                    "(SELECT count(*) FROM token_usages WHERE conversation_id=:id),"
                    "(SELECT count(*) FROM mcp_app_invocation_bindings WHERE conversation_id=:id)"
                ),
                {"id": graph.conversation_ids[0]},
            )
            assert descendants.one() == (0, 0, 0, 0, 0)
