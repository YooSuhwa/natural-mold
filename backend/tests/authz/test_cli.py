"""CLI commands require deliberate database selection and immutable model IDs."""

import pytest

from app.authz.cli import Arguments, run
from app.config import settings


@pytest.mark.parametrize("command", ["check", "explain"])
async def test_read_commands_reject_missing_pinned_model(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    # Given: neither a store nor a model has been pinned after bootstrap.
    monkeypatch.setattr(settings, "openfga_store_id", "")
    monkeypatch.setattr(settings, "openfga_model_id", "")
    # When / Then: never fall back to an unrelated server's latest model.
    with pytest.raises(ValueError, match="OPENFGA_STORE_ID"):
        await run(Arguments.model_validate({"command": command}))


async def test_bootstrap_requires_explicit_migration_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the default application database may be the shared developer DB.
    monkeypatch.setattr(settings, "migration_database_url", "")
    # When / Then: bootstrap requires an explicit schema-qualified installation target.
    with pytest.raises(ValueError, match="MIGRATION_DATABASE_URL"):
        await run(Arguments(command="bootstrap"))
