"""C-07 CLI: argument parsing, dry-run default, JSON output and exit codes."""

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import reconcile
from app.authz.cli import Arguments, parse, run, sync
from app.authz.reconcile import reconcile_store
from app.config import settings
from tests.authz.reconcile_testkit import seed_fixture
from tests.authz.revocation_testkit import _fresh_pending_hint  # noqa: F401 - autouse
from tests.conftest import TestSession


def test_parse_defaults_to_dry_run_and_reads_scope_flags():
    tenant = "00000000-0000-0000-0000-000000000001"
    args = parse(["reconcile", "--tenant", tenant, "--json"])
    assert (args.command, args.apply, args.as_json, str(args.tenant), args.org) == (
        "reconcile",
        False,
        True,
        tenant,
        None,
    )
    assert parse(["rebuild", "--apply"]).apply is True
    assert parse(["rebuild", "--dry-run"]).apply is False


@pytest.mark.parametrize(
    "argv",
    [
        ["reconcile", "--apply", "--dry-run"],
        ["rebuild", "--tenant", "00000000-0000-0000-0000-000000000001", "--org", "x"],
        ["reconcile", "--tenant", "not-a-uuid"],
        ["outbox"],
    ],
)
def test_parse_rejects_conflicting_or_invalid_flags(argv: list[str]):
    with pytest.raises(SystemExit) as raised:
        parse(argv)
    assert raised.value.code == 2


@pytest.mark.parametrize("command", ["rebuild", "reconcile"])
async def test_sync_commands_require_a_pinned_model(monkeypatch: pytest.MonkeyPatch, command):
    monkeypatch.setattr(settings, "openfga_store_id", "")
    with pytest.raises(ValueError, match="OPENFGA_STORE_ID"):
        await run(Arguments.model_validate({"command": command}))


async def test_exit_codes_follow_the_remaining_difference(
    db: AsyncSession, capsys: pytest.CaptureFixture[str]
):
    fx = await seed_fixture(db)
    sessions = reconcile.reconcile_sessions(TestSession)
    dry = Arguments(command="reconcile", as_json=True)
    assert await sync(dry, fx.store, sessions) == 1  # bootstrap not run yet: missing tuples
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "dry_run" and report["missing"] > 0 and report["queued"] == 0
    assert await sync(Arguments(command="reconcile", apply=True), fx.store, sessions) == 0
    assert "mode=repair" in capsys.readouterr().out
    assert (await reconcile_store(sessions, fx.store, now=fx.clock.now)).diff == 0
    assert await sync(Arguments(command="rebuild"), fx.store, sessions) == 0
    assert "missing=0 extra=0 changed=0" in capsys.readouterr().out


async def test_rebuild_apply_repairs_through_the_outbox(db: AsyncSession, capsys):
    fx = await seed_fixture(db)
    sessions = reconcile.reconcile_sessions(TestSession)
    args = Arguments(command="rebuild", apply=True, tenant=fx.world.tenant, as_json=True)
    assert await sync(args, fx.store, sessions) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "repair" and report["failed"] == 0
    assert len(report["outbox_ids"]) == report["queued"] > 0  # writes go through the outbox


async def test_failed_outbox_drain_exits_nonzero(db: AsyncSession, capsys):
    fx = await seed_fixture(db)
    sessions = reconcile.reconcile_sessions(TestSession)
    fx.store.failing_objects.add(f"tenant:{fx.world.tenant}")
    assert await sync(Arguments(command="reconcile", apply=True), fx.store, sessions) == 1
