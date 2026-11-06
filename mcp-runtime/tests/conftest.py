import base64
import json
import os
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import jwt
import pytest
import uvicorn
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

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


def seal(upstream: dict[str, Any], token_id: str, key: str = SECRET) -> str:
    """The backend's sealing (app.mcp_runtime.tokens.seal_upstream), for relay tokens."""
    derived = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=b"moldy-mcp-relay-v1"
    ).derive(key.encode())
    nonce = os.urandom(12)
    sealed = AESGCM(derived).encrypt(nonce, json.dumps(upstream).encode(), token_id.encode())
    return base64.urlsafe_b64encode(nonce + sealed).decode().rstrip("=")


def relay_token(value: dict[str, Any], *, sealed: str | None = None, **overrides: Any) -> str:
    token_id = str(uuid.uuid4())
    blob = sealed or seal(value, token_id)
    return make_token("relay", jti=token_id, upstream=blob, **overrides)


@contextmanager
def serve(app: Any) -> Iterator[str]:
    """Run an ASGI app on a free localhost port in a thread; yield its base URL."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture
def runtime_url(_secret: None) -> Iterator[str]:
    from moldy_mcp_runtime.app import create_app

    with serve(create_app()) as url:
        yield url
