"""Bounded provider requests. Keep upstream bodies and addresses out of public errors."""

from __future__ import annotations

import asyncio
import json
import ssl
from typing import Any

import httpx

from app.outbound.destination import OutboundBlockedError

_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class ResearchSearchError(ValueError):
    """A sanitized failure suitable for the tool runner's public error envelope."""

    def __init__(self, message: str, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


class SearchUnavailable(ResearchSearchError):
    """A transient failure for which one approved alternate may be tried."""


def _unavailable(status: int | None = None) -> SearchUnavailable:
    return SearchUnavailable("Research search is temporarily unavailable. Retry later.", status)


def _tls_failure(error: BaseException) -> bool:
    cause: BaseException | None = error
    while cause is not None:
        if isinstance(cause, ssl.SSLError):
            return True
        cause = cause.__cause__
    return False


def _require_success(response: httpx.Response) -> None:
    status = response.status_code
    if status in {500, 502, 503, 504}:
        raise _unavailable(status)
    if status in {401, 403}:
        raise ResearchSearchError(
            "Search provider denied access. Check provider authorization.", status
        )
    if status in {402, 429, 432, 433}:
        raise ResearchSearchError(
            "Search provider quota or rate limit reached. Check the limit.", status
        )
    if not response.is_success:
        raise ResearchSearchError(f"Search provider refused the request (HTTP {status}).", status)


async def provider_post(client: httpx.AsyncClient, url: str, **kwargs: Any) -> dict[str, Any]:
    body = bytearray()
    try:
        # A 307/308 must never forward a query or key to another destination.
        async with (
            asyncio.timeout(12),
            client.stream("POST", url, follow_redirects=False, **kwargs) as response,
        ):
            _require_success(response)
            async for chunk in response.aiter_bytes(chunk_size=65536):
                if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                    raise ResearchSearchError("Search provider response exceeds the maximum size.")
                body.extend(chunk)
    except TimeoutError:
        raise _unavailable() from None
    except OutboundBlockedError:
        raise ResearchSearchError(
            "Search destination was blocked. Contact an administrator."
        ) from None
    except (httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError) as error:
        if _tls_failure(error):
            raise ResearchSearchError("Search provider identity could not be verified.") from None
        raise _unavailable() from None
    except httpx.HTTPError:
        raise ResearchSearchError("Search provider request could not be sent.") from None

    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        raise ResearchSearchError("Search provider returned an invalid response.") from None
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ResearchSearchError("Search provider returned an invalid response.")
    return data
