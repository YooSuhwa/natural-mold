"""Route acceptance browser requests to one of two local API replicas."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SELECTOR = b"x-moldy-e2e-worker"
HOP_HEADERS = {
    b"connection",
    b"keep-alive",
    b"proxy-authenticate",
    b"proxy-authorization",
    b"proxy-connection",
    b"te",
    b"trailer",
    b"transfer-encoding",
    b"upgrade",
}
Headers = list[tuple[bytes, bytes]]


def tokens(headers: Headers, name: bytes) -> list[bytes]:
    return [
        token.strip().lower()
        for key, value in headers
        if key.lower() == name
        for token in value.split(b",")
        if token.strip()
    ]


def forwarded(headers: Headers) -> Headers:
    skip = HOP_HEADERS | {SELECTOR, *tokens(headers, b"connection")}
    return [(k.lower(), v) for k, v in headers if k.lower() not in skip]


def add_token(headers: Headers, name: bytes, token: bytes) -> None:
    if token not in tokens(headers, name):
        headers.append((name, token))


def local_origin(value: str) -> httpx.URL:
    url = httpx.URL(value)
    if (
        url.scheme != "http"
        or url.host not in {"127.0.0.1", "localhost", "::1"}
        or url.port is None
        or url.userinfo
        or url.raw_path != b"/"
        or url.fragment
    ):
        raise ValueError("API replicas must have plain loopback HTTP origins with explicit ports")
    return url


def endpoint_bucket(path: str) -> str:
    if "/ag-ui/" in path:
        end = path.rsplit("/", 1)[-1]
        return (
            f"ag-ui/{end}"
            if end in {"runs", "stream", "snapshot", "events", "attach"}
            else "ag-ui/other"
        )
    if "/langgraph/" in path:
        return "langgraph"
    if path.startswith("/api/auth/"):
        return "authentication"
    return "other"


async def reject(send: Send, status: int) -> None:
    await send({"type": "http.response.start", "status": status, "headers": []})
    await send({"type": "http.response.body", "body": b""})


class ReplicaRouter:
    def __init__(self, primary: str, secondary: str, receipt: Path | None) -> None:
        self.origins = {b"a": local_origin(primary), b"b": local_origin(secondary)}
        if self.origins[b"a"].port == self.origins[b"b"].port:
            raise ValueError("Expected two different API ports")
        self.receipt = receipt
        self.counts: Counter[tuple[str, str, str, int]] = Counter()
        # Use HTTPX's transport directly. It pools TCP connections without maintaining
        # an HTTP client's cross-request cookies or automatically following redirects.
        self.transport = httpx.AsyncHTTPTransport(retries=0)
        self.lifespan_app: ASGIApp = Starlette(lifespan=self.lifespan)

    @asynccontextmanager
    async def lifespan(self, _: Starlette) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await self.transport.aclose()
            if self.receipt:
                rows = [
                    {
                        "worker": w,
                        "method": m,
                        "endpoint": e,
                        "status_class": s,
                        "count": count,
                    }
                    for (w, m, e, s), count in sorted(self.counts.items())
                ]
                self.receipt.write_text(json.dumps({"requests": rows}, indent=2) + "\n")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.lifespan_app(scope, receive, send)
            return
        headers = scope["headers"]
        selection = [value for name, value in headers if name.lower() == SELECTOR]
        worker = selection[0] if selection else b"a"
        target = scope["raw_path"]
        if len(selection) > 1 or worker not in self.origins or not target.startswith(b"/"):
            await reject(send, 400)
            return
        if scope["query_string"]:
            target += b"?" + scope["query_string"]
        url = self.origins[worker].copy_with(raw_path=target)
        outbound = forwarded(headers)
        preflight_key = b"access-control-request-headers"
        allowed_header = b"access-control-allow-headers"
        control_preflight = scope["method"] == "OPTIONS" and SELECTOR in tokens(
            outbound, preflight_key
        )
        if control_preflight:
            requested = [h for h in tokens(outbound, preflight_key) if h != SELECTOR]
            outbound = [(k, v) for k, v in outbound if k != preflight_key]
            if requested:
                outbound.append((preflight_key, b", ".join(requested)))

        first = await receive()
        if first["type"] != "http.request":
            return
        consumed = asyncio.Event()

        async def body() -> AsyncIterator[bytes]:
            part: Message = first
            try:
                while True:
                    if part["type"] == "http.disconnect":
                        raise ConnectionError("Client disconnected during upload")
                    if part.get("body"):
                        yield part["body"]
                    if not part.get("more_body", False):
                        break
                    part = await receive()
            finally:
                consumed.set()

        content: bytes | AsyncIterator[bytes] = b""
        if first.get("body") or first.get("more_body", False):
            content = body()
        else:
            consumed.set()
        req = httpx.Request(
            scope["method"],
            url,
            headers=outbound,
            content=content,
            extensions={"timeout": {"connect": 5, "pool": 5, "write": 30, "read": None}},
        )

        async def until_disconnect() -> None:
            # Only the upload iterator reads ASGI request messages. Watch for a close
            # after it drains, including while the first response event is pending.
            await consumed.wait()
            while (await receive())["type"] != "http.disconnect":
                pass

        async def relay() -> None:
            try:
                upstream = await self.transport.handle_async_request(req)
            except (httpx.TransportError, ConnectionError):
                await reject(send, 502)
                return
            try:
                headers_out = forwarded(upstream.headers.raw)
                headers_out.append((SELECTOR, worker))
                if b"access-control-allow-origin" in {k for k, _ in headers_out}:
                    add_token(headers_out, b"access-control-expose-headers", SELECTOR)
                    if control_preflight and 200 <= upstream.status_code < 300:
                        add_token(headers_out, allowed_header, SELECTOR)
                method = scope["method"]
                if method not in {
                    "GET",
                    "POST",
                    "PUT",
                    "PATCH",
                    "DELETE",
                    "OPTIONS",
                    "HEAD",
                }:
                    method = "OTHER"
                self.counts[
                    (
                        worker.decode(),
                        method,
                        endpoint_bucket(scope["path"]),
                        upstream.status_code // 100,
                    )
                ] += 1
                await send(
                    {
                        "type": "http.response.start",
                        "status": upstream.status_code,
                        "headers": headers_out,
                    }
                )
                async for chunk in upstream.aiter_raw():
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                await send({"type": "http.response.body", "body": b""})
            finally:
                await upstream.aclose()

        response = asyncio.create_task(relay())
        disconnected = asyncio.create_task(until_disconnect())
        try:
            done, _ = await asyncio.wait(
                {response, disconnected}, return_when=asyncio.FIRST_COMPLETED
            )
            if response in done:
                await response
        finally:
            for task in (response, disconnected):
                if not task.done():
                    task.cancel()
            await asyncio.gather(response, disconnected, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", type=int)
    args = parser.parse_args()
    receipt = os.environ.get("MOLDY_AGUI_PROXY_RECEIPT")
    app = ReplicaRouter(
        os.environ["MOLDY_AGUI_PRIMARY_URL"],
        os.environ["MOLDY_AGUI_SECONDARY_URL"],
        Path(receipt) if receipt else None,
    )
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=args.port,
        access_log=False,
        log_level="warning",
        proxy_headers=False,
        server_header=False,
        date_header=False,
    )


if __name__ == "__main__":
    main()
