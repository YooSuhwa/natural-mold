"""HTTP origins used to match a configured gateway to an actual SDK request."""

import re
from ipaddress import IPv6Address
from typing import Annotated, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import AfterValidator

type StreamRetryProvider = Literal["openai", "openai_compatible", "openrouter"]
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def gateway_origin(url: object, *, origin_only: bool = False) -> str | None:
    if not isinstance(url, (str, httpx.URL)):
        return None
    value = str(url)
    if "\\" in value or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if any(char in parsed.netloc for char in "*@%") or parsed.netloc.endswith(":"):
            return None
        if origin_only and (parsed.path not in ("", "/") or "?" in value or "#" in value):
            return None
        host = httpx.URL(value).raw_host.decode("ascii")
        port = parsed.port
        if ":" in host:
            host = f"[{IPv6Address(host).compressed}]"
        else:
            domain = host.removesuffix(".")
            if len(domain) > 253 or not all(_HOST_LABEL.fullmatch(s) for s in domain.split(".")):
                return None
    except (ValueError, UnicodeError, httpx.InvalidURL):
        return None
    if port == 0:
        return None
    if port is not None and port != (443 if parsed.scheme == "https" else 80):
        host = f"{host}:{port}"
    return f"{parsed.scheme}://{host}"


def _configured_origin(value: str) -> str:
    origin = gateway_origin(value, origin_only=True)
    if origin is None:
        raise ValueError("expected an HTTP(S) origin with a host and optional port")
    return origin


type StreamRetryOrigin = Annotated[str, AfterValidator(_configured_origin)]
