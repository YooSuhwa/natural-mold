"""Legacy round-trip coverage for the deployable B08 migration."""

# ruff: noqa: S608 - this file builds UUID-only SQL for owned disposable databases.

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from alembic import command
from tests.authz.organization_migration_fixture import organization_lab

pytestmark = pytest.mark.integration


def _schema_signature(connection: Connection) -> tuple[tuple[str, ...], ...]:
    rows = connection.execute(
        text(
            "SELECT table_name, column_name, data_type, is_nullable "
            "FROM information_schema.columns WHERE table_schema='public' "
            "UNION ALL SELECT tc.table_name, tc.constraint_name, tc.constraint_type, "
            "COALESCE(rc.delete_rule, '') FROM information_schema.table_constraints tc "
            "LEFT JOIN information_schema.referential_constraints rc "
            "ON rc.constraint_schema=tc.constraint_schema "
            "AND rc.constraint_name=tc.constraint_name "
            "WHERE tc.table_schema='public' "
            "AND NOT (tc.constraint_type='CHECK' "
            "AND tc.constraint_name ~ '^[0-9]+_.*_not_null$') "
            "ORDER BY 1,2,3,4"
        )
    )
    return tuple(tuple(str(value) for value in row) for row in rows)


def _seed_m83_legacy_graph(connection: Connection) -> tuple[str, str]:
    user_id, tenant_id, org_id, model_id = (str(uuid4()) for _ in range(4))
    agent_id, conversation_id, run_id, input_id = (str(uuid4()) for _ in range(4))
    artifact_id, usage_id, deployment_id, thread_id, api_run_id = (str(uuid4()) for _ in range(5))
    trigger_id, trigger_run_id, server_id, tool_id, binding_id = (str(uuid4()) for _ in range(5))
    script = f"""
        INSERT INTO users (id,email,name,created_at) VALUES
          ('{user_id}','legacy-b08@example.test','Legacy B08',now());
        INSERT INTO tenants
          (id,name,slug,status,limits,settings,secrets_namespace,data_keys,created_at,updated_at)
        VALUES ('{tenant_id}','Legacy','legacy-{tenant_id}','active','{{}}','{{}}',
          'legacy/{tenant_id}','{{}}',now(),now());
        INSERT INTO organizations
          (id,tenant_id,name,slug,status,is_default,settings,authz_epoch,created_at,updated_at)
        VALUES ('{org_id}','{tenant_id}','Legacy','legacy','active',true,'{{}}',0,now(),now());
        INSERT INTO models
          (id,provider,model_name,display_name,is_default,is_visible,source,created_at)
        VALUES ('{model_id}','openai_compatible','legacy','Legacy',false,false,'manual',now());
        INSERT INTO agents
          (id,tenant_id,org_id,user_id,name,system_prompt,runtime_name,identity_mode,model_id,
           status,runtime_profile,is_favorite,created_at,updated_at)
        VALUES ('{agent_id}','{tenant_id}','{org_id}','{user_id}','Legacy agent','legacy',
          'legacy-{agent_id[:8]}','fixed','{model_id}','active','standard',false,now(),now());
        INSERT INTO conversations
          (id,tenant_id,org_id,agent_id,user_id,agent_name_snapshot,last_activity_at,title,source,
           is_pinned,created_at,updated_at,message_timestamps,unread_count,last_activity_source,
           queue_paused)
        VALUES ('{conversation_id}','{tenant_id}','{org_id}','{agent_id}','{user_id}',
          'Legacy agent',now(),'Legacy conversation','ui',false,now(),now(),'{{}}',0,'user',false);
        INSERT INTO conversation_runs
          (id,conversation_id,agent_id,user_id,source,status,is_active,created_at,updated_at)
        VALUES ('{run_id}','{conversation_id}','{agent_id}','{user_id}','chat','completed',false,
          now(),now());
        INSERT INTO conversation_run_inputs
          (id,conversation_id,agent_id,user_id,run_id,client_request_id,source,status,priority,
           position,revision,input_payload,attachment_ids,created_at,updated_at)
        VALUES ('{input_id}','{conversation_id}','{agent_id}','{user_id}','{run_id}','legacy',
          'ui','claimed',0,1,1,'{{}}','[]',now(),now());
        INSERT INTO conversation_artifacts
          (id,tenant_id,org_id,user_id,agent_id,conversation_id,assistant_msg_id,logical_path,
           display_name,mime_type,artifact_kind,size_bytes,sha256,status,is_favorite,
           preview_count,download_count,metadata_json,created_at,updated_at)
        VALUES ('{artifact_id}','{tenant_id}','{org_id}','{user_id}','{agent_id}',
          '{conversation_id}','legacy-message','legacy.bin','legacy.bin',
          'application/octet-stream','data',3,repeat('a',64),'ready',false,0,0,'{{}}',now(),now());
        INSERT INTO token_usages
          (id,run_id,conversation_id,agent_id,model_name,prompt_tokens,completion_tokens,
           total_tokens,created_at)
        VALUES ('{usage_id}','{run_id}','{conversation_id}','{agent_id}','legacy',1,1,2,now());
        INSERT INTO agent_deployments
          (id,tenant_id,org_id,agent_id,user_id,public_id,status,allow_streaming,
           allow_background,created_at,updated_at)
        VALUES ('{deployment_id}','{tenant_id}','{org_id}','{agent_id}','{user_id}',
          'legacy-deployment','active',true,false,now(),now());
        INSERT INTO agent_api_threads
          (id,public_id,user_id,deployment_id,conversation_id,created_at,updated_at)
        VALUES ('{thread_id}','legacy-thread','{user_id}','{deployment_id}',
          '{conversation_id}',now(),now());
        INSERT INTO agent_api_runs
          (id,public_id,user_id,deployment_id,thread_id,conversation_id,mode,status,created_at)
        VALUES ('{api_run_id}','legacy-api-run','{user_id}','{deployment_id}','{thread_id}',
          '{conversation_id}','sync','succeeded',now());
        INSERT INTO agent_triggers
          (id,tenant_id,org_id,agent_id,user_id,name,trigger_type,schedule_config,input_message,
           timezone,conversation_policy,status,run_count,failure_count,created_at,updated_at)
        VALUES ('{trigger_id}','{tenant_id}','{org_id}','{agent_id}','{user_id}',
          'Legacy trigger','interval','{{}}','legacy','Asia/Seoul','new_thread','paused',0,0,
          now(),now());
        INSERT INTO agent_trigger_runs
          (id,trigger_id,agent_id,user_id,conversation_id,status,source,input_message,started_at,
           created_at)
        VALUES ('{trigger_run_id}','{trigger_id}','{agent_id}','{user_id}','{conversation_id}',
          'success','scheduled','legacy',now(),now());
        INSERT INTO mcp_servers
          (id,tenant_id,org_id,user_id,name,transport,url,args,env_vars,headers,status,is_system,
           created_at,updated_at)
        VALUES ('{server_id}','{tenant_id}','{org_id}','{user_id}','Legacy MCP',
          'streamable_http','http://127.0.0.1.invalid','[]','{{}}','{{}}','disabled',false,
          now(),now());
        INSERT INTO mcp_tools
          (id,server_id,name,input_schema,metadata_json,enabled,created_at,updated_at)
        VALUES ('{tool_id}','{server_id}','legacy_tool','{{}}','{{}}',true,now(),now());
        INSERT INTO mcp_app_invocation_bindings
          (id,run_id,conversation_id,user_id,agent_id,credential_subject_user_id,mcp_server_id,
           mcp_tool_id,tool_call_id,remote_tool_name,resource_uri,tool_meta,created_at)
        VALUES ('{binding_id}','{run_id}','{conversation_id}','{user_id}','{agent_id}',
          '{user_id}','{server_id}','{tool_id}','legacy-call','legacy_tool','ui://legacy','{{}}',
          now());
        """
    connection.exec_driver_sql(script)
    return agent_id, conversation_id


def test_m83_roundtrip_preserves_legacy_graph_and_refuses_purged_downgrade() -> None:
    # Given a fresh owned database at actual m83 with a legacy retained graph.
    with organization_lab() as lab:
        command.upgrade(lab.config, "m83_org_scope_not_null")
        with lab.engine.begin() as connection:
            agent_id, conversation_id = _seed_m83_legacy_graph(connection)

        # When the deployable B08 revision is upgraded, downgraded and upgraded again.
        command.upgrade(lab.config, "b08_agent_lifecycle")
        with lab.engine.connect() as connection:
            first_signature = _schema_signature(connection)
            snapshots = connection.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM conversation_run_agents WHERE role='root'),"
                    "(SELECT deployment_public_id_snapshot FROM agent_api_threads LIMIT 1),"
                    "(SELECT deployment_public_id_snapshot FROM agent_api_runs LIMIT 1),"
                    "(SELECT trigger_name_snapshot FROM agent_trigger_runs LIMIT 1)"
                )
            ).one()
            assert snapshots == (1, "legacy-deployment", "legacy-deployment", "Legacy trigger")
        command.downgrade(lab.config, "m83_org_scope_not_null")
        with lab.engine.connect() as connection:
            legacy_counts = connection.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM conversations),"
                    "(SELECT count(*) FROM conversation_runs),"
                    "(SELECT count(*) FROM conversation_run_inputs),"
                    "(SELECT count(*) FROM conversation_artifacts),"
                    "(SELECT count(*) FROM token_usages),"
                    "(SELECT count(*) FROM agent_api_runs),"
                    "(SELECT count(*) FROM agent_trigger_runs),"
                    "(SELECT count(*) FROM mcp_app_invocation_bindings)"
                )
            ).one()
            assert legacy_counts == (1, 1, 1, 1, 1, 1, 1, 1)
        command.upgrade(lab.config, "b08_agent_lifecycle")
        with lab.engine.begin() as connection:
            assert _schema_signature(connection) == first_signature
            connection.execute(
                text(
                    "UPDATE conversations SET agent_id=NULL, agent_lifecycle_state='purged', "
                    "agent_purged_at=now() WHERE id=:conversation_id"
                ),
                {"conversation_id": conversation_id},
            )
            connection.execute(
                text("DELETE FROM agents WHERE id=:agent_id"), {"agent_id": agent_id}
            )

        # Then m83 downgrade refuses the now-unrepresentable purged history.
        with pytest.raises(RuntimeError, match="Cannot downgrade retained or purged"):
            command.downgrade(lab.config, "m83_org_scope_not_null")
        with lab.engine.connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert revision == "b08_agent_lifecycle"


def test_m83_orphan_conversation_backfills_as_purged_without_losing_history() -> None:
    # Given an m83 Conversation whose Agent was already physically removed.
    with organization_lab() as lab:
        command.upgrade(lab.config, "m83_org_scope_not_null")
        with lab.engine.begin() as connection:
            _, conversation_id = _seed_m83_legacy_graph(connection)
            connection.execute(
                text("UPDATE conversations SET agent_id=NULL WHERE id=:id"),
                {"id": conversation_id},
            )
            legacy = connection.execute(
                text("SELECT agent_id, agent_name_snapshot, title FROM conversations WHERE id=:id"),
                {"id": conversation_id},
            ).one()
            assert legacy == (None, "Legacy agent", "Legacy conversation")

        # When B08 upgrades the already-orphaned retained Conversation.
        command.upgrade(lab.config, "b08_agent_lifecycle")

        # Then the history root and immutable labels remain, with truthful purge state.
        with lab.engine.connect() as connection:
            retained = connection.execute(
                text(
                    "SELECT agent_lifecycle_state, agent_purged_at IS NOT NULL, "
                    "agent_name_snapshot, title, "
                    "(SELECT count(*) FROM conversation_runs WHERE conversation_id=:id), "
                    "(SELECT count(*) FROM conversation_artifacts WHERE conversation_id=:id) "
                    "FROM conversations WHERE id=:id"
                ),
                {"id": conversation_id},
            ).one()
            assert retained == (
                "purged",
                True,
                "Legacy agent",
                "Legacy conversation",
                1,
                1,
            )
