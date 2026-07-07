"""SEC-1 — web_scraper SSRF guard regression tests (network-free).

DNS is either avoided (IP-literal URLs), served from the hosts file
(``localhost``), or monkeypatched (``url_guard._resolve_host``); HTTP goes
through ``httpx.MockTransport``.
"""

from __future__ import annotations

import httpx
import pytest

from app.agent_runtime import tool_factory, url_guard
from app.agent_runtime.tool_factory import _build_web_scraper_tool
from app.agent_runtime.url_guard import BlockedUrlError, ensure_url_allowed

PUBLIC_IP = "93.184.216.34"


async def _run_scraper(monkeypatch: pytest.MonkeyPatch, handler, url: str) -> str:
    """Invoke the scraper tool against a MockTransport-backed client."""

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr(tool_factory, "get_tool_http_client", lambda: client)
        tool = _build_web_scraper_tool()
        assert tool.coroutine is not None
        return await tool.coroutine(url=url)


# ---------------------------------------------------------------------------
# ensure_url_allowed — unit level
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://127.0.0.1:8001/api/credentials",  # loopback
        "http://10.0.0.5/",  # RFC-1918
        "http://192.168.1.1/",  # RFC-1918
        "http://100.64.0.1/",  # carrier-grade NAT
        "http://[::1]/",  # IPv6 loopback
        "http://0.0.0.0/",  # unspecified
        "file:///etc/passwd",  # non-http scheme
        "ftp://93.184.216.34/",  # non-http scheme
        "http://",  # no host
    ],
)
async def test_ensure_url_allowed_blocks(url: str) -> None:
    with pytest.raises(BlockedUrlError):
        await ensure_url_allowed(url)


async def test_ensure_url_allowed_blocks_localhost_hostname() -> None:
    """Hostname path: localhost resolves (hosts file) to loopback → blocked."""

    with pytest.raises(BlockedUrlError):
        await ensure_url_allowed("http://localhost:8001/")


async def test_ensure_url_allowed_blocks_private_dns_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_resolve(host: str, port: int) -> list[str]:
        return ["10.0.0.7"]

    monkeypatch.setattr(url_guard, "_resolve_host", fake_resolve)
    with pytest.raises(BlockedUrlError):
        await ensure_url_allowed("http://internal.example.com/")


async def test_ensure_url_allowed_accepts_public(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(host: str, port: int) -> list[str]:
        return [PUBLIC_IP]

    monkeypatch.setattr(url_guard, "_resolve_host", fake_resolve)
    await ensure_url_allowed("https://example.com/page")  # no raise
    await ensure_url_allowed(f"http://{PUBLIC_IP}/page")  # IP literal, no DNS


# ---------------------------------------------------------------------------
# scrape_url — tool level
# ---------------------------------------------------------------------------


async def test_scraper_blocks_metadata_url_without_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text="<html>secret</html>")

    result = await _run_scraper(monkeypatch, handler, "http://169.254.169.254/latest/")
    assert result.startswith("Error: 허용되지 않는 주소입니다")
    assert calls == []  # blocked before any network I/O


async def test_scraper_blocks_redirect_to_private(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/"})

    result = await _run_scraper(monkeypatch, handler, f"http://{PUBLIC_IP}/start")
    assert result.startswith("Error: 허용되지 않는 주소입니다")
    assert len(calls) == 1  # first hop only; the private hop was never fetched


async def test_scraper_follows_public_redirect_and_extracts_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(301, headers={"location": f"http://{PUBLIC_IP}/final"})
        return httpx.Response(
            200,
            text="<html><script>evil()</script><body><p>본문 텍스트</p></body></html>",
        )

    result = await _run_scraper(monkeypatch, handler, f"http://{PUBLIC_IP}/start")
    assert "본문 텍스트" in result
    assert "evil" not in result


async def test_scraper_gives_up_after_max_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": f"http://{PUBLIC_IP}/again"})

    result = await _run_scraper(monkeypatch, handler, f"http://{PUBLIC_IP}/start")
    assert result.startswith("Error: 허용되지 않는 주소입니다")
    assert "redirect" in result


async def test_scraper_caps_body_size(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_factory, "_SCRAPE_MAX_BODY_BYTES", 64)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><p>" + "가" * 10_000 + "</p></html>")

    result = await _run_scraper(monkeypatch, handler, f"http://{PUBLIC_IP}/big")
    assert not result.startswith("Error:")
    assert len(result) < 200  # bounded read, far below the 10k payload
