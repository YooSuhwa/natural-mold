"""B-02 promotion must be live and independent of mutable application/draft metadata."""

import ast
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[2]


def test_scope_revisions_form_the_live_connected_m83_stage() -> None:
    # Given: the application's real Alembic directory, not the review draft registry.
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    # When: the deployable chain is resolved.
    revisions = {revision.revision: revision.down_revision for revision in script.walk_revisions()}
    # Then: the three reviewed steps are registered in their data-safe order.
    expected = {
        "m81_org_scope_columns": "m80_org_capabilities_invitations",
        "m82_default_org_backfill": "m81_org_scope_columns",
        "m83_org_scope_not_null": "m82_default_org_backfill",
    }
    assert expected.items() <= revisions.items()


def test_promoted_helpers_never_import_mutable_drafts_models_or_runtime_settings() -> None:
    # Given: the versioned migration-only helper package.
    package = BACKEND / "app/migrations/org_authz_v1"
    assert package.is_dir()
    sources = [
        *package.rglob("*.py"),
        *(BACKEND / "alembic/versions").glob("m8[123]_*.py"),
    ]
    # When: every Python import is parsed independently from the source.
    imports = []
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
            elif isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
    # Then: historical revisions cannot change through runtime schema/config evolution.
    assert not any(
        name.startswith(
            (
                "schema_drafts",
                "app.models",
                "app.database",
                "app.authz.config",
            )
        )
        for name in imports
    )
