"""Generic HTTP authentication driven by credential field interpolation.

A :class:`GenericAuth` declaration maps credential field values into one of four
buckets — ``headers``, ``params``, ``body``, ``basic`` — using the
``={{ $credentials.<field> }}`` interpolation grammar.

:func:`apply_authentication` produces a request-options dict ready for ``httpx``;
:class:`CredentialAuth` adapts the same logic to ``httpx.Auth`` for streaming
clients.

Algorithm/structure attribution: see NOTICES.md.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from app.credentials.interpolation import resolve_deep


@dataclass
class GenericAuth:
    """Declarative authentication recipe.

    ``properties`` is a mapping with up to four sections:

    - ``headers``: ``dict[str, str]`` — added to request headers
    - ``params``: ``dict[str, str]`` — added to query string
    - ``body``: ``dict[str, Any]``  — merged into JSON body
    - ``basic``: ``{"username": "...", "password": "..."}`` — RFC 7617 Basic auth

    Each leaf value is interpolated against the decrypted credential payload.
    """

    type: Literal["generic"] = "generic"
    properties: dict[str, dict[str, Any]] = field(default_factory=dict)


def _merge(target: dict[str, Any], extra: dict[str, Any]) -> None:
    for key, value in extra.items():
        target[key] = value


def apply_authentication(
    auth: GenericAuth | None,
    request_options: dict[str, Any],
    credentials: dict[str, Any],
) -> dict[str, Any]:
    """Return a *new* request-options dict with the auth recipe applied.

    The input is not mutated. Pass the result to ``httpx.AsyncClient.request``::

        opts = apply_authentication(definition.authenticate, base, decrypted)
        response = await client.request(**opts)
    """

    new_options: dict[str, Any] = {**request_options}
    if auth is None:
        return new_options

    sections = resolve_deep(auth.properties or {}, credentials)

    headers_in = dict(new_options.get("headers") or {})
    params_in = dict(new_options.get("params") or {})
    body_in = dict(new_options.get("json") or {})

    if isinstance(sections.get("headers"), dict):
        _merge(headers_in, sections["headers"])
    if isinstance(sections.get("params"), dict):
        _merge(params_in, sections["params"])
    if isinstance(sections.get("body"), dict):
        _merge(body_in, sections["body"])

    if isinstance(sections.get("basic"), dict):
        basic = sections["basic"]
        username = basic.get("username") or ""
        password = basic.get("password") or ""
        new_options["auth"] = (str(username), str(password))

    if headers_in:
        new_options["headers"] = headers_in
    if params_in:
        new_options["params"] = params_in
    if body_in:
        new_options["json"] = body_in

    return new_options


class CredentialAuth(httpx.Auth):
    """``httpx.Auth`` adapter that injects a :class:`GenericAuth` recipe.

    Useful when callers cannot rebuild a request-options dict (e.g. clients
    that are pre-constructed with a base URL and reused).
    """

    requires_request_body = False
    requires_response_body = False

    def __init__(
        self, auth: GenericAuth, credentials: dict[str, Any]
    ) -> None:
        self._sections = resolve_deep(auth.properties or {}, credentials)

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        headers = self._sections.get("headers")
        if isinstance(headers, dict):
            for key, value in headers.items():
                request.headers[str(key)] = str(value)

        params = self._sections.get("params")
        if isinstance(params, dict):
            existing = dict(request.url.params)
            existing.update({str(k): str(v) for k, v in params.items()})
            request.url = request.url.copy_with(params=existing)

        basic = self._sections.get("basic")
        if isinstance(basic, dict):
            username = str(basic.get("username") or "")
            password = str(basic.get("password") or "")
            request = next(
                httpx.BasicAuth(username, password).auth_flow(request)
            )

        yield request


__all__ = ["CredentialAuth", "GenericAuth", "apply_authentication"]
