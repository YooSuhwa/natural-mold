"""Native PostgreSQL migration and constraint coverage for B11 persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from alembic import command
from tests.authz.company_administration_migration_fixture import (
    INSERT_INVITATION,
    InvalidInvitationCase,
    company_migration_lane,
    invitation_values,
    rejects_invitation,
    seed_company,
)

pytestmark = pytest.mark.integration


def test_b08_upgrade_backfills_archive_timestamp_and_roundtrips() -> None:
    with company_migration_lane() as lane:
        command.downgrade(lane.config, "b08_agent_lifecycle")
        with lane.engine.begin() as connection:
            seed = seed_company(connection, archived=True)
            expected = connection.scalar(
                text("SELECT updated_at FROM organizations WHERE id=:id"), {"id": seed.org_id}
            )

        command.upgrade(lane.config, "b11_company_administration")
        with lane.engine.connect() as connection:
            observed = connection.scalar(
                text("SELECT archived_at FROM organizations WHERE id=:id"), {"id": seed.org_id}
            )
            assert observed == expected
        command.downgrade(lane.config, "b08_agent_lifecycle")
        command.upgrade(lane.config, "head")
        with lane.engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "b11_company_administration"
            )


def test_native_invitation_archive_and_materialization_constraints() -> None:
    with company_migration_lane() as lane, lane.engine.begin() as connection:
        seed = seed_company(connection)
        now = datetime.now(UTC)
        cases = (
            InvalidInvitationCase(
                "fk_tenant_invitations_tenant_id_tenants", (("tenant", uuid4()),)
            ),
            InvalidInvitationCase(
                "fk_tenant_invitations_created_by_users", (("created_by", uuid4()),)
            ),
            InvalidInvitationCase("ck_tenant_invitations_role", (("role", "member"),)),
            InvalidInvitationCase(
                "ck_tenant_invitations_accepted_actor_timestamp",
                (("accepted_by", seed.user_id),),
            ),
            InvalidInvitationCase(
                "ck_tenant_invitations_revoked_actor_timestamp",
                (("revoked_by", seed.user_id),),
            ),
            InvalidInvitationCase(
                "ck_tenant_invitations_terminal_state", (("status", "accepted"),)
            ),
            InvalidInvitationCase(
                "ck_tenant_invitations_terminal_state",
                (("status", "accepted"), ("accepted_at", now), ("revoked_at", now)),
            ),
            InvalidInvitationCase(
                "ck_tenant_invitations_expiration",
                (("expires_at", now), ("created_at", now)),
            ),
            InvalidInvitationCase(
                "ck_tenant_invitations_email_normalized",
                (("email", "Not-Normalized@example.test"),),
            ),
            InvalidInvitationCase("ck_tenant_invitations_token_hash", (("token_hash", "g" * 64),)),
        )
        for case in cases:
            rejects_invitation(connection, seed, case)

        accepted = invitation_values(seed)
        accepted.update(status="accepted", accepted_by=seed.user_id, accepted_at=now)
        connection.execute(INSERT_INVITATION, accepted)
        assert (
            connection.scalar(
                text("SELECT count(*) FROM tenant_members WHERE user_id=:id"),
                {"id": seed.user_id},
            )
            == 0
        )
        duplicate = invitation_values(seed)
        duplicate["token_hash"] = accepted["token_hash"]
        with (
            pytest.raises(IntegrityError, match="uq_tenant_invitations_token_hash"),
            connection.begin_nested(),
        ):
            connection.execute(INSERT_INVITATION, duplicate)
        connection.execute(text("DELETE FROM users WHERE id=:id"), {"id": seed.user_id})
        history = connection.execute(
            text(
                "SELECT status,created_by,accepted_by,created_at IS NOT NULL,"
                "accepted_at IS NOT NULL FROM tenant_invitations WHERE id=:id"
            ),
            {"id": accepted["id"]},
        ).one()
        assert history == ("accepted", None, None, True, True)

        revoker_id = uuid4()
        connection.execute(
            text("INSERT INTO users (id,email,name,created_at) VALUES (:id,:email,'Revoke',now())"),
            {"id": revoker_id, "email": f"{revoker_id}@example.test"},
        )
        revoked = invitation_values(seed)
        revoked.update(
            created_by=revoker_id,
            status="revoked",
            revoked_by=revoker_id,
            revoked_at=now,
        )
        connection.execute(INSERT_INVITATION, revoked)
        connection.execute(text("DELETE FROM users WHERE id=:id"), {"id": revoker_id})
        revoked_history = connection.execute(
            text(
                "SELECT status,created_by,revoked_by,created_at IS NOT NULL,"
                "revoked_at IS NOT NULL FROM tenant_invitations WHERE id=:id"
            ),
            {"id": revoked["id"]},
        ).one()
        assert revoked_history == ("revoked", None, None, True, True)

        with (
            pytest.raises(IntegrityError, match="ck_organizations_archive_state"),
            connection.begin_nested(),
        ):
            connection.execute(
                text("UPDATE organizations SET status='archived' WHERE id=:id"),
                {"id": seed.org_id},
            )

        history_user_id, conversation_id, run_id = uuid4(), uuid4(), uuid4()
        connection.execute(
            text("INSERT INTO users (id,email,name,created_at) VALUES (:id,:email,'Run',now())"),
            {"id": history_user_id, "email": f"{history_user_id}@example.test"},
        )
        connection.execute(
            text(
                "INSERT INTO conversations "
                "(id,tenant_id,org_id,agent_id,user_id,agent_name_snapshot,last_activity_at,"
                "title,source,is_pinned,created_at,updated_at,message_timestamps,unread_count,"
                "last_activity_source,queue_paused,agent_lifecycle_state,agent_purged_at) "
                "VALUES (:id,:tenant,:org,NULL,:user,'Purged',now(),'History','ui',false,now(),"
                "now(),'{}',0,'user',false,'purged',now())"
            ),
            {
                "id": conversation_id,
                "tenant": seed.tenant_id,
                "org": seed.org_id,
                "user": history_user_id,
            },
        )
        with (
            pytest.raises(
                IntegrityError, match="ck_conversation_runs_runtime_materialization_pair"
            ),
            connection.begin_nested(),
        ):
            connection.execute(
                text(
                    "INSERT INTO conversation_runs "
                    "(id,conversation_id,user_id,source,status,is_active,"
                    "runtime_materialized_at,created_at,updated_at) "
                    "VALUES (:id,:conversation,:user,'chat','running',true,now(),now(),now())"
                ),
                {"id": run_id, "conversation": conversation_id, "user": history_user_id},
            )
        with (
            pytest.raises(
                IntegrityError, match="ck_conversation_runs_runtime_materialization_sha256"
            ),
            connection.begin_nested(),
        ):
            connection.execute(
                text(
                    "INSERT INTO conversation_runs "
                    "(id,conversation_id,user_id,source,status,is_active,"
                    "runtime_materialized_at,runtime_materialization_sha256,"
                    "created_at,updated_at) VALUES "
                    "(:id,:conversation,:user,'chat','completed',false,now(),:digest,"
                    "now(),now())"
                ),
                {
                    "id": uuid4(),
                    "conversation": conversation_id,
                    "user": history_user_id,
                    "digest": "g" * 64,
                },
            )
        connection.execute(
            text(
                "INSERT INTO conversation_runs "
                "(id,conversation_id,user_id,source,status,is_active,"
                "runtime_materialized_at,runtime_materialization_sha256,created_at,updated_at) "
                "VALUES (:id,:conversation,:user,'chat','completed',false,now(),:digest,"
                "now(),now())"
            ),
            {
                "id": run_id,
                "conversation": conversation_id,
                "user": history_user_id,
                "digest": "a" * 64,
            },
        )
