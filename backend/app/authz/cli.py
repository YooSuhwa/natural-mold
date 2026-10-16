"""Operator CLI: uv run python -m app.authz.cli bootstrap|rebuild|reconcile|check|explain.

Exit codes: 0 success / allow / no remaining difference; 1 deny, or the store still differs
from Postgres after the command (a dry run that found a difference, or queued repairs that
failed to drain); 2 usage errors (argparse). Held tuples never change the exit code.
"""

import argparse
from typing import Literal, assert_never
from uuid import UUID

import anyio
from openfga_sdk.client import OpenFgaClient
from openfga_sdk.client.models.expand_request import ClientExpandRequest
from pydantic import BaseModel, JsonValue, TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.authz.bootstrap import BootstrapResult, bootstrap
from app.authz.client import CheckQuery, FgaClient, open_client, sdk_configuration
from app.authz.reconcile import (
    Mode,
    ReconcileReport,
    Sessions,
    TupleStore,
    reconcile_sessions,
    reconcile_store,
)
from app.config import settings


class Arguments(BaseModel):
    command: Literal["bootstrap", "rebuild", "reconcile", "check", "explain"]
    user: str = ""
    relation: str = ""
    object: str = ""
    tenant: UUID | None = None
    org: UUID | None = None
    apply: bool = False
    as_json: bool = False


class BootstrapOutput(BaseModel):
    model: BootstrapResult
    tuples: ReconcileReport


def _require_pinned_model() -> None:
    if not settings.openfga_store_id or not settings.openfga_model_id:
        raise ValueError("Set OPENFGA_STORE_ID and OPENFGA_MODEL_ID from bootstrap")


def _print(report: ReconcileReport, as_json: bool) -> None:
    if as_json:
        print(report.model_dump_json())
        return
    print(
        f"{report.scope} mode={report.mode} expected={report.expected} stored={report.stored} "
        f"missing={report.missing} extra={report.extra} changed={report.changed} "
        f"held={report.held} queued={report.queued} failed={report.failed}"
    )
    for name in ("missing", "extra", "changed", "held"):
        for key in getattr(report, f"sample_{name}"):
            print(f"  {name}: {key}")


async def sync(args: Arguments, store: TupleStore, sessions: Sessions | None = None) -> int:
    """``rebuild``/``reconcile``: dry run unless ``--apply``; repairs go through the outbox.

    Held tuples (recent outbox activity or revocation markers) are reported but never
    change the exit code; the next run picks them up.
    """
    mode: Mode = "repair" if args.apply else "dry_run"
    report = await reconcile_store(
        sessions or reconcile_sessions(),
        store,
        mode=mode,
        tenant_id=args.tenant,
        org_id=args.org,
        drain=True,
    )
    _print(report, args.as_json)
    failed = report.failed or report.skipped  # skipped: another repair holds the lock
    return 1 if (report.diff and mode == "dry_run") or failed else 0


async def run(args: Arguments) -> int:
    match args.command:
        case "bootstrap":
            if not settings.migration_database_url:
                raise ValueError("Set MIGRATION_DATABASE_URL to the migrated installation database")
            engine = create_async_engine(settings.migration_database_url)
            try:

                def sessions() -> AsyncSession:
                    return AsyncSession(engine)

                async with OpenFgaClient(sdk_configuration(settings)) as sdk:
                    async with sessions() as db:
                        with anyio.fail_after(30):
                            result = await bootstrap(db, sdk, settings)
                            await db.commit()
                    sdk.set_store_id(result.store_id)  # fill writes to the model just pinned
                    sdk.set_authorization_model_id(result.model_id)
                    store = FgaClient(sdk, settings.openfga_timeout_ms)
                    report = await reconcile_store(sessions, store, mode="fill", drain=True)
                print(BootstrapOutput(model=result, tuples=report).model_dump_json())
            finally:
                await engine.dispose()
        case "rebuild" | "reconcile":
            _require_pinned_model()
            async with open_client(settings) as client:
                return await sync(args, client)
        case "check":
            _require_pinned_model()
            query = CheckQuery(user=args.user, relation=args.relation, object=args.object)
            async with open_client(settings) as client:
                allowed = await client.check(query, consistency="HIGHER_CONSISTENCY")
            print("allow" if allowed else "deny")
            return 0 if allowed else 1
        case "explain":
            _require_pinned_model()
            async with OpenFgaClient(sdk_configuration(settings)) as sdk:
                with anyio.fail_after(settings.openfga_timeout_ms / 1000):
                    result = await sdk.expand(
                        ClientExpandRequest(relation=args.relation, object=args.object)
                    )
            tree = TypeAdapter(dict[str, JsonValue]).validate_python(result.tree.to_dict())
            print(TypeAdapter(dict[str, JsonValue]).dump_json(tree).decode())
        case _:
            assert_never(args.command)
    return 0


def _sync_parser(
    commands: "argparse._SubParsersAction[argparse.ArgumentParser]", name: str, help_text: str
) -> None:
    parser = commands.add_parser(name, help=help_text)
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--tenant", type=UUID, help="Limit to one company")
    where.add_argument("--org", type=UUID, help="Limit to one organization")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Repair (default: dry run)")
    mode.add_argument("--dry-run", dest="apply", action="store_false", help="Report only")
    parser.add_argument("--json", dest="as_json", action="store_true", help="JSON report")
    parser.set_defaults(apply=False)


def parse(argv: list[str] | None = None) -> Arguments:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "bootstrap", help="Deploy the model, then write every expected tuple (m84 schema)"
    )
    _sync_parser(commands, "rebuild", "Rewrite the store from Postgres (direct writes)")
    _sync_parser(commands, "reconcile", "Diff the store; --apply queues repairs in the outbox")
    check = commands.add_parser(
        "check", help="Evaluate one permission at the configured pinned model"
    )
    check.add_argument("--user", required=True)
    check.add_argument("--relation", required=True)
    check.add_argument("--object", required=True)
    explain = commands.add_parser(
        "explain", help="Expand a relation tree; this is not a permission check"
    )
    explain.add_argument("--relation", required=True)
    explain.add_argument("--object", required=True)
    return Arguments.model_validate(vars(parser.parse_args(argv)))


def main() -> int:
    return anyio.run(run, parse())


if __name__ == "__main__":
    raise SystemExit(main())
