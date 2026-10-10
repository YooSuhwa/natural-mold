"""A second PostgreSQL writer cannot commit during the verified rollback window."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError

from app.database import Base
from schema_drafts.org_authz import backfill_provenance as provenance
from schema_drafts.org_authz import m78_tenants_organizations as m78
from schema_drafts.org_authz import m79_groups as m79
from schema_drafts.org_authz import m80_org_capabilities_invitations as m80
from schema_drafts.org_authz import m81_org_scope_columns as m81
from schema_drafts.org_authz import m82_default_org_backfill as m82
from schema_drafts.org_authz.backfill_snapshot import BackfillSnapshot
from schema_drafts.org_authz.schema import schema_metadata
from tests.authz.backfill_fixture import seed
from tests.authz.test_schema_draft_migrations import execute

pytestmark = pytest.mark.integration


def test_postgres_writer_cannot_commit_between_verification_and_restoration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a committed m82 migration in a fresh, uniquely named owned schema.
    engine = sa.create_engine(os.environ["INTEGRATION_DATABASE_URL"])
    schema = "authz_rollback_lock_" + uuid4().hex
    preflight_complete, release_restore = Event(), Event()
    restore = provenance.restore
    metadata = schema_metadata(scope_columns=True)
    conversation = metadata.tables["conversations"]
    event = metadata.tables["message_events"]
    created = False

    def paused_restore(
        connection: Connection,
        source: sa.MetaData,
        snapshot: BackfillSnapshot,
    ) -> None:
        preflight_complete.set()
        assert release_restore.wait(10), "writer probe never released rollback"
        restore(connection, source, snapshot)

    def rollback() -> None:
        with engine.begin() as connection:
            connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            execute(connection, m82.downgrade)

    try:
        with engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            Base.metadata.create_all(connection)
            for operation in (m78.upgrade, m79.upgrade, m80.upgrade, m81.upgrade):
                execute(connection, operation)
            seed(connection, metadata)
            execute(connection, m82.upgrade)
            conversation_id = connection.scalar(sa.select(conversation.c.id))
            assert conversation_id is not None
        created = True
        monkeypatch.setattr(provenance, "restore", paused_restore)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(rollback)
            try:
                assert preflight_complete.wait(10), "rollback did not reach its verified window"

                # When: a real second transaction inserts an FK child after preflight.
                def conflicting_writer() -> None:
                    with engine.begin() as writer:
                        writer.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
                        writer.execute(sa.text("SET LOCAL lock_timeout = '300ms'"))
                        writer.execute(
                            sa.insert(event).values(
                                id=uuid4(),
                                conversation_id=conversation_id,
                                assistant_msg_id=str(uuid4()),
                                events=[],
                            )
                        )

                with pytest.raises(OperationalError, match="lock timeout"):
                    conflicting_writer()
            finally:
                release_restore.set()
            pending.result(timeout=10)
        # Then: no concurrent event committed, and verified rollback restored its originals.
        with engine.begin() as connection:
            connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            assert connection.scalar(sa.select(sa.func.count()).select_from(event)) == 0
            assert (
                connection.execute(
                    sa.select(
                        conversation.c.user_id,
                        conversation.c.org_id,
                        conversation.c.tenant_id,
                    )
                ).all()
                == [(None, None, None)] * 4
            )
    finally:
        release_restore.set()
        if created:
            with engine.begin() as connection:
                connection.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()
