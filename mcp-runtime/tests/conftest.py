import socket
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
import uvicorn

SECRET = "test-secret-" + "x" * 32


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_RUNTIME_TOKEN_SECRET", SECRET)


def make_token(server: str = "system/diagnostics", **overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": "moldy-backend",
        "aud": "moldy-mcp-runtime",
        "sub": str(uuid.uuid4()),
        "iat": now,
        "exp": now + 300,
        "jti": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "agent_id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "org_id": str(uuid.uuid4()),
        "server": server,
    }
    claims.update(overrides)
    key = claims.pop("_key", SECRET)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="HS256")


@pytest.fixture
def runtime_url(_secret: None) -> Iterator[str]:
    from moldy_mcp_runtime.app import create_app

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)
