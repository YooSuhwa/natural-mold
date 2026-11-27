"""Run the real application with loopback-only research providers for browser acceptance."""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import gettempdir
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import FastAPI
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agent_runtime import tool_factory
from app.agent_runtime.e2e_scripted_model import E2EScriptedChatModel
from app.agent_runtime.model_provider_registry import PROVIDER_MAP
from app.config import settings
from app.outbound.client import outbound_http_client

SEARXNG_URL = "https://searxng.example.test/search"
TAVILY_URL = "https://api.tavily.com/search"
SOURCE_URL = "https://evidence.example.org/reports/research-update"
SOURCE_TITLE = "Updated research bulletin"
TAVILY_KEY = "local-fixture-key"
SEARCH_QUERY = "research bulletin"
PERIODS = {
    "E2E_RESEARCH_FALLBACK": "month",
    "E2E_RESEARCH_WEEK": "week",
    "E2E_RESEARCH_BLOCKED": "month",
}


class ResearchScriptedModel(E2EScriptedChatModel):
    """Pick a bound tool deterministically; the worker still executes the installed skill's tool."""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        text = self._latest_human_text(messages)
        marker = next((name for name in PERIODS if name in text), None)
        if marker is None:
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        if not {"read_file", "research_search"} <= set(self._bound_tool_names):
            raise ValueError("Updated skill is missing its mounted file or research_search tool")
        last = messages[-1]
        if isinstance(last, ToolMessage) and last.name == "research_search":
            content = (
                "Research fixture stopped."
                if last.status == "error"
                else "Research fixture complete."
            )
            message = AIMessage(content=content)
        elif isinstance(last, ToolMessage):
            if (
                last.name != "read_file"
                or last.status == "error"
                or "version: 0.2.0" not in last.text
                or "`research_search`" not in last.text
            ):
                raise ValueError("The mounted Deep Research instructions must be version 0.2.0")
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "research_search",
                        "id": f"call_{marker.lower()}",
                        "args": {
                            "query": SEARCH_QUERY,
                            "time_range": PERIODS[marker],
                            "max_results": 3,
                        },
                    }
                ],
            )
        else:
            system = "\n".join(msg.text for msg in messages if isinstance(msg, SystemMessage))
            advertised = re.search(r"(/runtime/[^`\s<>]*/skills/[^`<>\n]+?/SKILL\.md)", system)
            if advertised is None:
                raise ValueError("Deep Research file path was not advertised to the model")
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "id": f"read_{marker.lower()}",
                        "args": {"file_path": advertised.group(1)},
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


@contextmanager
def provider_server(port: int) -> Iterator[int]:
    published_date = (datetime.now(UTC) - timedelta(days=1)).date().isoformat() + "T00:00:00+00:00"
    calls: list[dict[str, str | int]] = []
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            # Observations contain only the provider, status and requested period.
            pass

        def reply(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 -- stdlib handler
            if self.path != "/observations":
                self.reply(404, {})
                return
            with lock:
                snapshot = list(calls)
            self.reply(200, {"calls": snapshot, "published_date": published_date})

        def do_POST(self) -> None:  # noqa: N802 -- stdlib handler
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 8192:
                self.reply(400, {})
                return
            body = self.rfile.read(size)
            if self.path == "/tavily":
                data = json.loads(body)
                valid = (
                    data.get("query") == SEARCH_QUERY
                    and data.get("max_results") == 3
                    and data.get("filter_by_published_date") is True
                    and self.headers.get("Authorization") == f"Bearer {TAVILY_KEY}"
                )
                provider, period = "tavily", data.get("time_range", "")
                status, payload = (503 if valid else 400), {"detail": "Fixture unavailable"}
            elif self.path == "/searxng":
                form = parse_qs(body.decode())
                valid = (
                    form.get("format") == ["json"]
                    and form.get("q") == [SEARCH_QUERY]
                    and "Authorization" not in self.headers
                )
                provider, period = "searxng", form.get("time_range", [""])[0]
                status, payload = (
                    (200 if valid else 400),
                    {
                        "results": [
                            {
                                "title": SOURCE_TITLE,
                                "url": SOURCE_URL,
                                "content": "Published research update from the primary source.",
                                "publishedDate": published_date,
                                "engine": "fixture",
                            }
                        ]
                    },
                )
            else:
                self.reply(404, {})
                return
            with lock:
                calls.append({"provider": provider, "status": status, "time_range": period})
            self.reply(status, payload)

    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            thread.join()


class LoopbackProviderTransport(httpx.AsyncBaseTransport):
    """Replace only the wire destinations, after Moldy's normal outbound guard."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.wire = httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = {TAVILY_URL: "/tavily", SEARXNG_URL: "/searxng"}.get(str(request.url))
        if path is None or request.method != "POST":
            raise AssertionError("Unconfigured provider request in isolated research fixture")
        # Build the loopback request from scratch; its socket can only connect to the fixture.
        headers = {
            name: request.headers[name]
            for name in ("Content-Type", "Authorization")
            if name in request.headers
        }
        local = httpx.Request(
            "POST",
            f"http://127.0.0.1:{self.port}{path}",
            headers=headers,
            content=await request.aread(),
        )
        return await self.wire.handle_async_request(local)

    async def aclose(self) -> None:
        await self.wire.aclose()


def _require_isolated_stack() -> None:
    runtime = urlsplit(settings.database_url)
    checkpoint = urlsplit(settings.database_url_sync)
    data = Path(settings.data_root).resolve()
    # Reject the default/shared installation before the application opens either database.
    if not (
        settings.app_env == "dev"
        and settings.e2e_test_helpers_enabled
        and settings.e2e_scripted_model_enabled
        and settings.e2e_seed_user_enabled
        and os.environ.get("MOLDY_DISABLE_ENV_FILE", "").lower() in {"1", "true"}
        and runtime.scheme == "postgresql+asyncpg"
        and checkpoint.scheme == "postgresql"
        and runtime.hostname in {"127.0.0.1", "localhost"}
        and runtime.port is not None
        and (runtime.hostname, runtime.port, runtime.path)
        == (checkpoint.hostname, checkpoint.port, checkpoint.path)
        and runtime.path.startswith("/moldy_e2e_scripted_research_")
        and not (runtime.query or checkpoint.query)
        and Path(settings.data_root).is_absolute()
        and data.name == "data"
        and data.parent.name.startswith("moldy-research-e2e-")
        and any(
            data.is_relative_to(root) and data != root
            for root in (Path("/tmp").resolve(), Path(gettempdir()).resolve())
        )
    ):
        raise RuntimeError(
            "A dedicated loopback research E2E database and temporary data root are required"
        )


def create_app() -> FastAPI:
    """Uvicorn factory for the research browser lane. Installs no product routes."""
    _require_isolated_stack()
    from app.main import create_app as create_moldy_app

    app = create_moldy_app()
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        with provider_server(int(os.environ["E2E_RESEARCH_FIXTURE_PORT"])) as port:
            async with outbound_http_client(
                transport=LoopbackProviderTransport(port), ssrf_guard=True
            ) as client:
                with (
                    patch.object(tool_factory, "_TOOL_HTTP_CLIENT", client),
                    patch.object(settings, "tavily_api_key", TAVILY_KEY),
                    patch.dict(PROVIDER_MAP, {"e2e_scripted": ResearchScriptedModel}),
                ):
                    async with original_lifespan(application):
                        yield

    app.router.lifespan_context = lifespan
    return app
