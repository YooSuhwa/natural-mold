"""Crash a real notification scheduler between the recipient INSERT and the outbox commit."""

import asyncio
import logging
import os
import signal
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import text

from tests.authz.i06_rls_lab import RlsLab, async_url

_BACKEND = Path(__file__).resolve().parents[2]


async def _poll_once() -> None:
    from app.scheduler import release_scheduler_leader, try_acquire_scheduler_leader
    from app.scheduler_job_ids import LeaderSchedulerJobId
    from app.scheduler_runtime import run_leader_scheduler_job

    assert await try_acquire_scheduler_leader(), "notification worker could not take leadership"
    try:
        await run_leader_scheduler_job(LeaderSchedulerJobId.CHAT_NOTIFICATION_DRAIN)
    finally:
        await release_scheduler_leader()


def _restrict_to_database() -> None:
    dsn = urlsplit(os.environ["DATABASE_URL"])
    assert dsn.hostname
    port = dsn.port or 5432
    # Observe actual outbound sends in both processes without replacing any application service.
    addresses = {ip[4][0] for ip in socket.getaddrinfo(dsn.hostname, port)}

    def require_database(event: str, args: tuple[object, ...]) -> None:
        if event == "socket.connect" and isinstance(args[1], tuple):
            host, target_port = args[1][:2]
            if host not in addresses or target_port != port:
                Path("unexpected-notification-network").touch()
                raise PermissionError(
                    "notifier attempted a connection outside its PostgreSQL database"
                )

    sys.addaudithook(require_database)


@asynccontextmanager
async def notification_poller(
    lab: RlsLab, scratch: Path
) -> AsyncIterator[asyncio.subprocess.Process]:
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(_BACKEND),
        "DATABASE_URL": async_url(lab.app_url),
        "DATABASE_URL_SYNC": lab.app_url.set(drivername="postgresql").render_as_string(
            hide_password=False
        ),
        "DB_RLS_ENABLED": "true",
        "ARTIFACT_STORAGE_DIR": str(scratch / "store"),
        "ENCRYPTION_KEYS": "01" * 32,
        "JWT_SECRET": "notice-poller-native-test-only-secret",
    }
    worker = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.integration.notification_process",
        env=env,
        cwd=scratch,
    )
    try:
        yield worker
    finally:
        if worker.returncode is None:
            worker.kill()
        await worker.wait()


async def kill_after_notice_flush(lab: RlsLab, outbox_id: UUID, scratch: Path) -> tuple[int, int]:
    # The trigger checks that the real recipient INSERT has succeeded in this transaction.
    # A separate observer can witness a blocked lock request even though the INSERT is uncommitted.
    # The lab is a disposable database. Only the chosen transition can reach the barrier.
    with lab.admin.begin() as ddl:
        ddl.exec_driver_sql("""
            CREATE FUNCTION hold_notification_ack() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM notifications WHERE id = NEW.notification_id
                      AND target_id = NEW.id AND user_id = NEW.user_id
                ) THEN RAISE EXCEPTION 'notice was not flushed before the delivery acknowledgement';
                END IF;
                PERFORM pg_advisory_xact_lock(hashtextextended(NEW.id::text, 18701));
                RETURN NEW;
            END $$""")
        ddl.exec_driver_sql(f"""
            CREATE TRIGGER hold_notification_ack BEFORE UPDATE OF status ON chat_notification_outbox
            FOR EACH ROW WHEN (NEW.id = '{outbox_id}' AND NEW.status = 'delivered')
            EXECUTE FUNCTION hold_notification_ack()""")
    try:
        with lab.admin.connect().execution_options(isolation_level="AUTOCOMMIT") as control:
            control.execute(
                text("SELECT pg_advisory_lock(hashtextextended(:target, 18701))"),
                {"target": str(outbox_id)},
            )
            try:
                async with notification_poller(lab, scratch) as worker, asyncio.timeout(45):
                    observed = text("""
                        SELECT pid FROM pg_stat_activity WHERE datname = current_database()
                        AND pg_backend_pid() = ANY(pg_blocking_pids(pid))
                    """)
                    while not (backend_pid := control.scalar(observed)):
                        if worker.returncode is not None:
                            raise AssertionError(f"notification poller exited {worker.returncode}")
                        await asyncio.sleep(0.05)
                    # Read committed isolation on the observer must still see the old state.
                    assert control.execute(
                        text("""
                        SELECT status, notification_id,
                            (SELECT count(*) FROM notifications WHERE target_id = :target)
                        FROM chat_notification_outbox WHERE id = :target
                    """),
                        {"target": outbox_id},
                    ).one() == ("pending", None, 0)
                    worker.kill()
                    exit_code = await worker.wait()
                    assert exit_code != 0
                    if os.name == "posix":
                        assert exit_code == -signal.SIGKILL
                    worker_pid = worker.pid
                    logging.getLogger(__name__).info(
                        "Stopped notification process pid=%d db_pid=%d exit=%d at the ack barrier",
                        worker_pid,
                        backend_pid,
                        exit_code,
                    )
            finally:
                control.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:target, 18701))"),
                    {"target": str(outbox_id)},
                )
        return worker_pid, backend_pid
    finally:
        # DDL waits for the killed connection to finish rolling back.
        with lab.admin.begin() as ddl:
            ddl.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
            ddl.exec_driver_sql("DROP TRIGGER hold_notification_ack ON chat_notification_outbox")
            ddl.exec_driver_sql("DROP FUNCTION hold_notification_ack()")


if __name__ == "__main__":
    _restrict_to_database()
    asyncio.run(_poll_once())
