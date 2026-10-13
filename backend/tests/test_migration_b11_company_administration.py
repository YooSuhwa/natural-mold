from __future__ import annotations

import hashlib
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

IMMUTABLE_REVISIONS = {
    "m78_tenants_organizations.py": (
        "d1b84089fa60092983d4b26aabce1ee8b7d842324e7ba7c133dd31dfc0ee50a3"
    ),
    "m79_groups.py": ("d9d88f0a6eee1a987a78bfd86fe563e1f112d05ca67bccf62b1fb38cb863947e"),
    "m80_org_capabilities_invitations.py": (
        "a62f35a143d992d02da47399609eb5e8b5020dd7238142a98c96db43e5d7891b"
    ),
    "m81_org_scope_columns.py": "f125744871e9772ac3e603dc1b90c9f6d0e351a1ae634738bd42ff17be137bdf",
    "m82_default_org_backfill.py": (
        "97bb95685195d12a2ab77ddfce8cf27eba689d8d4e21872d9e9b8c98c9b01e79"
    ),
    "m83_org_scope_not_null.py": "d8cdf2ac04f7dd0ebfc95451e884e2c322fa37859d18d89265317b150ec6afc9",
    "b08_agent_lifecycle.py": "c31f69cb9129b29628a2ed701f302a1117d2e2d881bea49744f00fa25c283b22",
}


def test_b11_is_the_single_head_after_immutable_b08() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))

    assert script.get_heads() == ["b11_company_administration"]
    revision = script.get_revision("b11_company_administration")
    assert revision is not None
    assert revision.down_revision == "b08_agent_lifecycle"


def test_historical_organization_and_b08_revisions_are_immutable() -> None:
    versions = Path("alembic/versions")

    observed = {
        name: hashlib.sha256((versions / name).read_bytes()).hexdigest()
        for name in IMMUTABLE_REVISIONS
    }

    assert observed == IMMUTABLE_REVISIONS
