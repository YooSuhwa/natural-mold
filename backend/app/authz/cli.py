"""Operator CLI: uv run python -m app.authz.cli bootstrap|check|explain."""

import argparse
from typing import Literal, assert_never

import anyio
from openfga_sdk.client import OpenFgaClient
from openfga_sdk.client.models.expand_request import ClientExpandRequest
from pydantic import BaseModel, JsonValue, TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.authz.bootstrap import bootstrap
from app.authz.client import CheckQuery, open_client, sdk_configuration
from app.config import settings


class Arguments(BaseModel):
    command: Literal["bootstrap", "check", "explain"]
    user: str = ""
    relation: str = ""
    object: str = ""


async def run(args: Arguments) -> int:
    match args.command:
        case "bootstrap":
            if not settings.migration_database_url:
                raise ValueError("Set MIGRATION_DATABASE_URL to the migrated installation database")
            engine = create_async_engine(settings.migration_database_url)
            try:
                async with (
                    AsyncSession(engine) as db,
                    OpenFgaClient(sdk_configuration(settings)) as sdk,
                ):
                    with anyio.fail_after(30):
                        result = await bootstrap(db, sdk, settings)
                        await db.commit()
                    print(result.model_dump_json())
            finally:
                await engine.dispose()
        case "check":
            if not settings.openfga_store_id or not settings.openfga_model_id:
                raise ValueError("Set OPENFGA_STORE_ID and OPENFGA_MODEL_ID from bootstrap")
            query = CheckQuery(user=args.user, relation=args.relation, object=args.object)
            async with open_client(settings) as client:
                allowed = await client.check(query, consistency="HIGHER_CONSISTENCY")
            print("allow" if allowed else "deny")
            return 0 if allowed else 1
        case "explain":
            if not settings.openfga_store_id or not settings.openfga_model_id:
                raise ValueError("Set OPENFGA_STORE_ID and OPENFGA_MODEL_ID from bootstrap")
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("bootstrap", help="Deploy and record canonical model; requires m84 schema")
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
    return anyio.run(run, Arguments.model_validate(vars(parser.parse_args())))


if __name__ == "__main__":
    raise SystemExit(main())
