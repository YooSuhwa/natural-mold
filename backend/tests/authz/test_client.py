"""Exercise the real SDK over HTTP, including retries and failure observability."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic import SecretStr

from app.authz import client
from app.authz.config import AuthzSettings

STORE = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
MODEL = "01ARZ3NDEKTSV4RRFFQ69G5FAW"


class FgaWire:
    """Wire fixture mutates response scenarios and request observations."""

    def __init__(self) -> None:
        self.statuses: list[int] = []
        self.requests: list[str] = []
        self.auth_headers: list[str | None] = []
        self.wait = threading.Event()
        self.wait.set()
        self.server: ThreadingHTTPServer | None = None
        self.payload = b'{"allowed":true}'


@pytest.fixture
def wire() -> Iterator[tuple[FgaWire, AuthzSettings]]:
    fixture = FgaWire()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 — stdlib HTTP handler protocol
            self.rfile.read(int(self.headers["Content-Length"]))
            fixture.requests.append(self.path)
            fixture.auth_headers.append(self.headers.get("Authorization"))
            fixture.wait.wait(timeout=2)
            status = fixture.statuses.pop(0) if fixture.statuses else 200
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                self.wfile.write(fixture.payload if status == 200 else b'{"code":"internal_error"}')
            except (BrokenPipeError, ConnectionResetError):
                return  # The timeout scenario intentionally closes the client socket.

        def log_message(self, format: str, *args: str | int) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    fixture.server = server
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = AuthzSettings(
        openfga_api_url=f"http://127.0.0.1:{server.server_port}",
        openfga_api_token=SecretStr("dummy-wire-token"),
        openfga_store_id=STORE,
        openfga_model_id=MODEL,
    )
    yield fixture, config
    fixture.wait.set()
    server.shutdown()
    thread.join(timeout=3)
    server.server_close()


async def test_check_retries_when_server_returns_transient_failure(wire):
    # Given: one transient server failure followed by a real SDK response.
    fixture, config = wire
    fixture.statuses = [503, 200]
    # When: permission is checked through the pinned SDK adapter.
    async with client.open_client(config) as fga:
        result = await fga.check(
            client.CheckQuery(user="user:u", relation="can_run", object="agent:a")
        )
        # Then: retries stay bounded and one successful operation is observed.
        assert result is True
        assert fga.metrics.counts[("check", "success")] == 1
    assert len(fixture.requests) == 2
    assert fixture.auth_headers == ["Bearer dummy-wire-token"] * 2


async def test_check_fails_closed_when_server_never_responds_in_budget(wire):
    # Given: the server blocks until the test releases it; no arbitrary sleeps.
    fixture, config = wire
    fixture.wait.clear()
    config.openfga_timeout_ms = 40
    # When: an authorization request exceeds its entire operation budget.
    async with client.open_client(config) as fga:
        with pytest.raises(client.AuthzUnavailable):
            await fga.check(client.CheckQuery(user="user:u", relation="can_run", object="agent:a"))
        # Then: failure is explicit and observable, never an implicit allow.
        assert fga.metrics.counts[("check", "error")] == 1


async def test_batch_maps_correlation_ids_when_response_order_is_reversed(wire):
    # Given: FGA returns checks in a different order than the request.
    fixture, config = wire
    fixture.payload = json.dumps(
        {"result": {"1": {"allowed": False}, "0": {"allowed": True}}}
    ).encode()
    queries = (
        client.CheckQuery(user="user:u", relation="can_run", object="agent:a"),
        client.CheckQuery(user="user:u", relation="can_run", object="agent:b"),
    )
    # When: the batch completes.
    async with client.open_client(config) as fga:
        result = await fga.batch_check(queries)
    # Then: each answer stays associated with the original check.
    assert result == (True, False)


async def test_batch_fails_closed_when_any_check_has_error(wire):
    # Given: one item failed in a successful HTTP batch response.
    fixture, config = wire
    fixture.payload = b'{"result":{"0":{"error":{"input_error":"validation_error"}}}}'
    # When / Then: partial batch errors cannot become a cached allow.
    async with client.open_client(config) as fga:
        with pytest.raises(client.AuthzUnavailable):
            await fga.batch_check(
                (client.CheckQuery(user="user:u", relation="can_run", object="agent:a"),)
            )
