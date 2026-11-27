"""Authenticated actors, server responses and read-only database observations for the cutover gate."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row


class GateFailure(AssertionError):
    """A public API or the database disagreed with the expected behavior."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise GateFailure(reason)


def expect_status(response: httpx.Response, expected: int) -> None:
    require(
        response.status_code == expected,
        f"{response.request.method} {response.request.url.path}: "
        f"expected HTTP {expected}, received {response.status_code}",
    )


def _local_endpoint(url: str) -> str:
    parsed = urlsplit(url)
    require(
        parsed.scheme in {"http", "https"}
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        and not parsed.username
        and parsed.path in {"", "/"},
        "the cutover gate requires a loopback HTTP service",
    )
    return url.rstrip("/")


@dataclass
class Participant:
    client: httpx.AsyncClient
    csrf: dict[str, str]
    user_id: str

    async def send(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        csrf: bool = True,
        timeout: float = 90.0,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        require(
            path.startswith("/") and not path.startswith("//"), "expected an API path"
        )
        # Idle timeouts reset on every SSE heartbeat. A synthetic turn must also finish
        # inside the requested wall-clock budget.
        async with asyncio.timeout(timeout):
            return await self.client.request(
                method,
                path,
                json=body,
                timeout=timeout,
                headers={**(self.csrf if csrf else {}), **(headers or {})},
            )

    async def json(
        self, method: str, path: str, body: Any = None, *, expected: int = 200
    ) -> Any:
        response = await self.send(method, path, body)
        expect_status(response, expected)
        return response.json()


@dataclass(frozen=True)
class Frame:
    id: str | None
    data: dict[str, Any]


def sse_frames(response: httpx.Response) -> list[Frame]:
    """Validate each event with the installed AG-UI Python SDK, retaining its opaque SSE id."""
    import json

    from ag_ui.core import Event, RunAgentInput
    from pydantic import TypeAdapter

    expect_status(response, 200)
    if response.request.method == "POST":
        RunAgentInput.model_validate_json(response.request.content)
    require(
        response.headers.get("content-type", "").split(";")[0] == "text/event-stream",
        "expected text/event-stream",
    )
    parser = TypeAdapter(Event)
    frames = []
    data = []
    event_id: str | None = None
    for line in [*response.text.splitlines(), ""]:
        if not line:
            if data:
                event = json.loads("\n".join(data))
                parser.validate_python(event)
                frames.append(Frame(event_id, event))
            data = []
            event_id = None
            continue
        field, separator, value = line.partition(":")
        if not separator:
            continue
        value = value.removeprefix(" ")
        if field == "data":
            data.append(value)
        elif field == "id":
            event_id = value
    require(bool(frames), "empty AG-UI transcript")
    return frames


def checked_sse(response: httpx.Response) -> list[dict[str, Any]]:
    return [frame.data for frame in sse_frames(response)]


@dataclass
class Gate:
    owner_a: Participant
    owner_b: Participant
    other_b: Participant
    dsn: str
    tenant_id: str
    other_tenant_id: str
    evidence_dir: Path
    model_id: str

    check_sse = staticmethod(checked_sse)

    async def create_chat(
        self,
        label: str,
        *,
        agent_overrides: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        definition = {
            "name": f"AG-UI {label} {uuid4().hex[:8]}",
            "system_prompt": "Answer this local scripted acceptance scenario.",
            "model_id": self.model_id,
            **(agent_overrides or {}),
        }
        agent = await self.owner_a.json("POST", "/api/agents", definition, expected=201)
        aid = str(UUID(agent["id"]))
        thread = await self.owner_a.json(
            "POST",
            f"/api/agents/{aid}/conversations",
            {"title": label},
            expected=201,
        )
        return aid, str(UUID(thread["id"]))

    async def sql(
        self,
        statement: str,
        params: Sequence[Any] = (),
        *,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        async with await psycopg.AsyncConnection.connect(
            self.dsn, row_factory=dict_row
        ) as conn:
            await conn.execute("SET TRANSACTION READ ONLY")
            await conn.execute(
                "SELECT set_config('app.tenant_id', %s, true)",
                (tenant_id or self.tenant_id,),
            )
            cursor = await conn.execute(statement, params)
            return await cursor.fetchall()


@asynccontextmanager
async def open_gate() -> AsyncIterator[Gate]:
    a = _local_endpoint(os.environ["MOLDY_AGUI_PRIMARY_URL"])
    b = _local_endpoint(os.environ["MOLDY_AGUI_SECONDARY_URL"])
    require(a != b, "independent worker endpoints required")
    dsn = os.environ["DATABASE_URL_SYNC"]
    parsed = conninfo_to_dict(dsn)
    require(
        parsed.get("host") in {"localhost", "127.0.0.1", "::1"}
        and parsed.get("dbname", "").startswith("moldy_e2e_scripted"),
        "expected the dedicated loopback scripted PostgreSQL database",
    )
    evidence = Path(os.environ["MOLDY_AGUI_EVIDENCE_DIR"])
    evidence.mkdir(parents=True, exist_ok=True)

    async with AsyncExitStack() as stack:

        async def login(url: str, email: str, password: str) -> Participant:
            client = await stack.enter_async_context(
                httpx.AsyncClient(
                    base_url=url, trust_env=False, follow_redirects=False, timeout=90
                ),
            )
            response = await client.post(
                "/api/auth/login", json={"email": email, "password": password}
            )
            expect_status(response, 200)
            body = response.json()
            require(
                "moldy_at" in client.cookies and "moldy_csrf" in client.cookies,
                "signed session cookies missing",
            )
            return Participant(
                client, {"X-CSRF-Token": body["csrf_token"]}, body["user"]["id"]
            )

        owner_a = await login(
            a, os.environ["E2E_USER_EMAIL"], os.environ["E2E_USER_PASSWORD"]
        )
        owner_b = await login(
            b, os.environ["E2E_USER_EMAIL"], os.environ["E2E_USER_PASSWORD"]
        )
        other_b = await login(
            b,
            os.environ["MOLDY_AGUI_OTHER_EMAIL"],
            os.environ["MOLDY_AGUI_OTHER_PASSWORD"],
        )
        require(
            owner_a.user_id == owner_b.user_id != other_b.user_id, "wrong actor pairing"
        )
        primary_tenant = str(UUID(os.environ["MOLDY_AGUI_PRIMARY_TENANT_ID"]))
        other_tenant = str(UUID(os.environ["MOLDY_AGUI_OTHER_TENANT_ID"]))
        require(
            primary_tenant != other_tenant, "expected actors from distinct companies"
        )
        models = await owner_a.json("GET", "/api/models")
        seeded = [
            m["id"]
            for m in models
            if m["provider"] == "e2e_scripted"
            and m["model_name"] == "document-artifact-scripted"
        ]
        require(len(seeded) == 1, "expected one seeded scripted document model")
        yield Gate(
            owner_a,
            owner_b,
            other_b,
            dsn,
            primary_tenant,
            other_tenant,
            evidence,
            seeded[0],
        )
