from typing import Final

import pytest

from app.services.mcp_apps_service import MAX_APP_HTML_BYTES, McpAppsError, _resource_html

OSM_ROUTE_RESOURCE_BYTES: Final = 561_195


def test_resource_html_accepts_current_osm_route_app_size() -> None:
    html = "x" * OSM_ROUTE_RESOURCE_BYTES

    assert len(_resource_html({"text": html}).encode()) == OSM_ROUTE_RESOURCE_BYTES


def test_resource_html_rejects_payload_above_configured_limit() -> None:
    with pytest.raises(McpAppsError) as oversized:
        _resource_html({"text": "x" * (MAX_APP_HTML_BYTES + 1)})

    assert oversized.value.code == "mcp_app_resource_too_large"
    assert oversized.value.status_code == 413
