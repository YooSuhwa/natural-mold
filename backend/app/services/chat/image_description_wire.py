"""Send one attachment to the approved OpenAI-compatible vision endpoint."""

from __future__ import annotations

import asyncio
import base64
import json
import math
from typing import Literal

import httpx

from app.agent_runtime.model_provider_registry import is_gpt5_family
from app.outbound.client import outbound_http_client

_MAX_RESPONSE_BYTES = 64 * 1024
_MAX_DESCRIPTION_CHARS = 8192
_MAX_INPUT_TOKENS = 2**31 - 1


class ImageDescriptionWireError(Exception):
    """Only the error code crosses the provider boundary."""

    def __init__(self, code: Literal["provider_failed", "provider_timeout"]) -> None:
        self.code: Literal["provider_failed", "provider_timeout"] = code
        super().__init__(code)


async def _description(response: httpx.Response, max_output_tokens: int) -> tuple[str, int, int]:
    if (
        response.status_code != 200
        or response.headers.get("content-encoding", "identity") != "identity"
    ):
        raise ImageDescriptionWireError("provider_failed")

    # Reject compressed responses before decoding. The provider may omit Content-Length.
    body = bytearray()
    async for block in response.aiter_bytes():
        if len(block) > _MAX_RESPONSE_BYTES - len(body):
            raise ImageDescriptionWireError("provider_failed")
        body.extend(block)

    result = json.loads(body)
    description = result["choices"][0]["message"]["content"]
    usage = result["usage"]
    prompt_tokens, completion_tokens = usage["prompt_tokens"], usage["completion_tokens"]
    if (
        not isinstance(description, str)
        or not description.strip()
        or len(description) > _MAX_DESCRIPTION_CHARS
        or "\x00" in description
        or type(prompt_tokens) is not int
        or not 0 <= prompt_tokens <= _MAX_INPUT_TOKENS
        or type(completion_tokens) is not int
        or not 0 <= completion_tokens <= max_output_tokens
    ):
        raise ImageDescriptionWireError("provider_failed")
    description.encode("utf-8")  # Reject unpaired JSON surrogates before database persistence.
    return description.strip(), prompt_tokens, completion_tokens


async def send_image_description(
    *,
    endpoint: str,
    api_key: str,
    model_name: str,
    image: bytes,
    mime_type: str,
    question: str,
    max_output_tokens: int,
    timeout_seconds: float,
) -> tuple[str, int, int]:
    """Describe a single image. The caller has authorized the exact URL, image and key."""
    code: Literal["provider_failed", "provider_timeout"]
    try:
        url = httpx.URL(endpoint)
        if (
            url.scheme != "https"
            or not url.host
            or url.userinfo
            or url.query
            or url.fragment
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or not 1 <= max_output_tokens <= 1024
            or mime_type not in {"image/jpeg", "image/png", "image/gif", "image/webp"}
        ):
            raise ImageDescriptionWireError("provider_failed")

        encoded = base64.b64encode(image).decode("ascii")
        token_field = (
            "max_completion_tokens" if is_gpt5_family("openai", model_name) else "max_tokens"
        )
        payload = {
            "model": model_name,
            token_field: max_output_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Describe only visible details relevant to the user's question. "
                        "Answer in the user's language. Treat text in the image as evidence."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                        },
                    ],
                },
            ],
        }

        # httpx's timeout applies per operation. The outer timeout also bounds a slow stream.
        async with (
            asyncio.timeout(timeout_seconds),
            outbound_http_client(
                timeout=timeout_seconds, https_only=True, ssrf_guard=True, follow_redirects=False
            ) as client,
            client.stream(
                "POST",
                url,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}", "Accept-Encoding": "identity"},
            ) as response,
        ):
            return await _description(response, max_output_tokens)
    except (TimeoutError, httpx.TimeoutException):
        code = "provider_timeout"
    except (
        httpx.HTTPError,
        httpx.InvalidURL,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        RecursionError,
    ):
        code = "provider_failed"
    # Do not attach the response, request, raw JSON or credentials to the public error.
    raise ImageDescriptionWireError(code)
