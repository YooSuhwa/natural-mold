"""The staged organization migrations must remain a single linear upgrade path."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_migrations_have_single_head_when_loaded():
    # Given: this checkout's complete revision directory.
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "alembic"))
    # When: Alembic resolves all heads.
    heads = ScriptDirectory.from_config(config).get_heads()
    # Then: deployment needs exactly one deterministic migration path.
    assert len(heads) == 1
