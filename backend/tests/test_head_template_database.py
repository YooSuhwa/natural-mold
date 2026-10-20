"""The lane's head template is keyed so a changed migration never reuses a stale schema."""

from pathlib import Path

from tests.authz.head_template_database import (
    SCHEMA_INPUTS,
    schema_inputs_digest,
    template_name,
)


def _tree(root: Path) -> tuple[Path, ...]:
    versions = root / "alembic" / "versions"
    versions.mkdir(parents=True)
    (versions / "m1_first.py").write_text("revision = 'm1'\n")
    env = root / "alembic" / "env.py"
    env.write_text("# env\n")
    return (versions, env)


def test_digest_tracks_migration_bytes_names_and_additions(tmp_path: Path) -> None:
    inputs = _tree(tmp_path)
    baseline = schema_inputs_digest(inputs, tmp_path)
    assert schema_inputs_digest(inputs, tmp_path) == baseline
    (inputs[0] / "m1_first.py").write_text("revision = 'm1'  # edited\n")
    edited = schema_inputs_digest(inputs, tmp_path)
    assert edited != baseline
    (inputs[0] / "m2_second.py").write_text("revision = 'm2'\n")
    added = schema_inputs_digest(inputs, tmp_path)
    assert added not in {baseline, edited}
    (inputs[0] / "m2_second.py").rename(inputs[0] / "m2_renamed.py")
    assert schema_inputs_digest(inputs, tmp_path) not in {baseline, edited, added}
    inputs[1].write_text("# env changed\n")
    assert schema_inputs_digest(inputs, tmp_path) != added


def test_digest_covers_the_real_chain_and_its_shared_helpers() -> None:
    names = {path.name for path in SCHEMA_INPUTS}
    assert names == {"versions", "env.py", "migrations"}
    assert all(path.exists() for path in SCHEMA_INPUTS)


def test_template_name_is_a_bounded_lane_identifier_keyed_by_every_input() -> None:
    name = template_name("i06_company_rls", "a" * 64, rls_enabled=False)
    assert name.startswith("moldy_pg_lane_tpl_") and len(name) <= 63
    assert name.replace("_", "").isalnum()
    assert name == template_name("i06_company_rls", "a" * 64, rls_enabled=False)
    others = {
        template_name("i05_other", "a" * 64, rls_enabled=False),
        template_name("i06_company_rls", "b" * 64, rls_enabled=False),
        template_name("i06_company_rls", "a" * 64, rls_enabled=True),
    }
    assert name not in others and len(others) == 3
