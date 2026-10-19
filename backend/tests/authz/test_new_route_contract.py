"""New schema changes require an intentional review of the checked-in API contract."""

import json
from pathlib import Path

from tests.authz.new_route_contract import new_route_contract


def test_new_navigation_api_matches_reviewed_openapi_snapshot() -> None:
    snapshot = Path(__file__).with_name("openapi_new_routes.json")
    assert new_route_contract() == json.loads(snapshot.read_text())
