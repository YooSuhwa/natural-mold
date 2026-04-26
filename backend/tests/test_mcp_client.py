from __future__ import annotations

import pytest

from app.agent_runtime.mcp_client import (
    _build_probe_headers,
    extract_transport_headers,
)
from app.agent_runtime.mcp_client import (
    test_mcp_connection as mcp_test_connection,
)

# ---------------------------------------------------------------------------
# Header helpers — pure function 단위 테스트.
# probe 자체는 mcp library의 streamable-http transport에 위임하므로 외부 호출
# 모킹 가치가 낮음 — 헤더 합성/추출 로직만 회귀 방어한다.
# ---------------------------------------------------------------------------


class TestExtractTransportHeaders:
    def test_none_input_returns_none(self):
        assert extract_transport_headers(None) is None

    def test_no_headers_key_returns_none(self):
        assert extract_transport_headers({"url": "https://x"}) is None

    def test_non_dict_headers_returns_none(self):
        assert extract_transport_headers({"headers": "not a dict"}) is None

    def test_dict_headers_returns_str_only(self):
        assert extract_transport_headers(
            {"headers": {"X-Tenant": "acme", "X-Bad": 123, "X-Empty": None}}
        ) == {"X-Tenant": "acme"}

    def test_all_non_str_returns_none(self):
        assert extract_transport_headers({"headers": {"X-Bad": 123}}) is None


class TestBuildProbeHeaders:
    def test_no_auth_no_extra_returns_none(self):
        assert _build_probe_headers(None, None) is None

    def test_extra_headers_only(self):
        assert _build_probe_headers(None, {"X-Tenant": "acme"}) == {"X-Tenant": "acme"}

    def test_auth_config_default_authorization_header(self):
        headers = _build_probe_headers({"api_key": "secret"}, None)
        assert headers == {"Authorization": "secret"}

    def test_auth_config_custom_header_name(self):
        headers = _build_probe_headers({"api_key": "secret", "header_name": "X-API-Key"}, None)
        assert headers == {"X-API-Key": "secret"}

    def test_auth_config_without_api_key_ignored(self):
        # api_key 없으면 auth_config 전체 무시 — 빈 헤더 반환
        assert _build_probe_headers({"header_name": "X-API-Key"}, None) is None

    def test_extra_and_auth_merged(self):
        headers = _build_probe_headers({"api_key": "tok"}, {"X-Tenant": "acme"})
        assert headers == {"X-Tenant": "acme", "Authorization": "tok"}

    def test_auth_overrides_extra_with_same_header(self):
        # auth_config가 extra_headers 뒤에 적용되어 동일 키는 덮어쓴다 (legacy 호환)
        headers = _build_probe_headers(
            {"api_key": "winner", "header_name": "X-Auth"},
            {"X-Auth": "loser"},
        )
        assert headers == {"X-Auth": "winner"}


# ---------------------------------------------------------------------------
# probe failure — streamablehttp_client에 도달 불가능한 URL → success=False.
# 외부 호출은 발생하지만 즉시 connection error로 떨어진다 (실제 네트워크 X).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_unreachable_returns_failure():
    # localhost의 닫힌 포트로 즉시 connection refused.
    result = await mcp_test_connection("http://127.0.0.1:1/nonexistent")
    assert result["success"] is False
    assert "error" in result
    assert result["tools"] == []
