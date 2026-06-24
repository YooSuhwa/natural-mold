"""Run-scoped secret value collection — ADR-021 (value-based trace redaction).

A single agent run injects real plaintext secrets (LLM ``api_key``, MCP
transport headers, ``{{$credentials.x}}`` interpolated values, skill
credential bindings) into tool inputs/outputs and state snapshots. Those
secrets are *known* at run time — the credential system resolved them — so
trace egress can mask them by **exact substring** instead of guessing with
key/value heuristics.

This module owns the run-scoped set of plaintext secrets and propagates it
to :func:`app.agent_runtime.protocol_redaction.redact_protocol_data` via a
:class:`contextvars.ContextVar`. The ContextVar is set at the top of
``_run_agent_stream`` and lazily unioned in ``_prepare_runtime_components``
(skill credentials resolve later, including for subagents which share the
same run task).

Design notes (ADR-021 §2):

* The set is stored as a plain ``set[str]`` (NOT ``frozenset``) so the lazy
  skill-credential path can union new values in place via the same object the
  ContextVar already references — subagents inherit the mutation.
* ``copy_context()``-spawned fire-and-forget DB writers observe the value the
  ContextVar held *at copy time*, so persistence stays masked.
* No DB, no logging, no interpolation here — pure value plumbing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from app.agent_runtime.runtime_config import AgentConfig

# Values shorter than this are low signal — masking 4-char fragments would
# scrub user-visible output for no benefit. Mirrors marketplace
# ``_MIN_REDACT_LEN`` (ADR-017 / ADR-021 Open Question #1 resolved at 5).
_MIN_SECRET_LEN = 5

# HTTP auth header schemes whose token body is often echoed *without* the
# scheme prefix (e.g. a tool logs only the bearer token). Collect the body
# after the scheme so the bare token is masked too.
_AUTH_SCHEME_PREFIXES = ("Bearer ", "Basic ")

# Canonical serialized header / param pair shape: ``{"name"|"key"|"header":
# <meta>, "value": <secret>}`` (MCP headers, tool params). The name/key/header
# field is metadata, not a secret — collecting it over-masks common header
# names like ``Authorization`` when they appear in legitimate prose (ADR-021).
_PAIR_META_KEYS = ("name", "key", "header")

_run_secrets: ContextVar[set[str] | None] = ContextVar("moldy_run_secrets", default=None)


def set_run_secrets(values: Iterable[str] | None) -> Token[set[str] | None]:
    """Install the run-scoped secret set and return the reset token.

    A ``None`` / empty input still installs an (empty) set so the lazy
    :func:`add_run_secrets` path always has a live object to union into.
    """

    current: set[str] = set(values) if values else set()
    return _run_secrets.set(current)


def reset_run_secrets(token: Token[set[str] | None]) -> None:
    """Restore the ContextVar to its pre-:func:`set_run_secrets` state."""

    _run_secrets.reset(token)


def get_run_secrets() -> set[str] | None:
    """Return the current run-scoped secret set, or ``None`` when unset.

    ``None`` (no active run / unit tests / trigger mode) means value-based
    masking is a no-op and only the heuristics run — preserving legacy
    behaviour.
    """

    return _run_secrets.get()


def add_run_secrets(values: Iterable[str] | None) -> None:
    """Union freshly resolved plaintext secrets into the active run set.

    No-op when the ContextVar is unset (the run never opted in) or when
    ``values`` is empty. Mutates the existing set in place so subagents and
    already-copied contexts observe the new values.
    """

    if not values:
        return
    current = _run_secrets.get()
    if current is None:
        return
    current.update(v for v in collect_secret_values(values))


def collect_secret_values(obj: object) -> set[str]:
    """Recursively flatten ``obj`` into the set of plaintext secret strings.

    Walks dicts (values only), lists/tuples/sets, and string leaves. Keeps
    only ``str`` leaves with at least :data:`_MIN_SECRET_LEN` chars. For
    ``"Bearer <token>"`` / ``"Basic <token>"`` style values, also adds the
    token body after the scheme prefix so the bare body is masked even when
    echoed without the scheme.

    Pure and allocation-light — no DB, no regex. Bytes are ignored (secrets
    are injected as ``str``).
    """

    out: set[str] = set()
    _collect_into(obj, out)
    return out


def collect_url_userinfo(value: object, out: set[str]) -> None:
    """Add username/password embedded in a ``scheme://user:pw@host`` URL.

    ADR-021 M1 — base URLs and MCP endpoints may carry credentials in the
    userinfo component (``postgres://user:pw@host``, ``https://tok@host``).
    Only the *username* and *password* are collected (parsed with
    :func:`urllib.parse.urlsplit`) so we never over-mask the host / path /
    scheme. Non-string / unparseable values are ignored.
    """

    if not isinstance(value, str) or "://" not in value:
        return
    try:
        parts = urlsplit(value)
    except ValueError:
        return
    for component in (parts.username, parts.password):
        if isinstance(component, str) and len(component) >= _MIN_SECRET_LEN:
            out.add(component)


def collect_cfg_secret_values(cfg: AgentConfig, *, extra: object = None) -> set[str]:
    """Gather every eager plaintext secret injected into ``cfg``'s run.

    ADR-021 §1 (eager set) — the shared collector every run entrypoint calls
    so value-based redaction sees the run's *actual* secrets. Covers:

    * LLM ``api_key`` + ``provider_api_keys``.
    * Each tool config's plaintext ``credentials`` dict.
    * MCP ``mcp_transport_headers`` (Authorization / Cookie; the ``collect``
      core also splits the ``Bearer``/``Basic`` token body).
    * **M1** — userinfo embedded in ``cfg.base_url`` + every
      ``model_fallback_chain[*]["base_url"]`` (username/password only).
    * **M1** — credentials embedded in an MCP tool config ``url``
      (``tool_config["url"]`` userinfo).

    Skill-binding credentials resolve later and union in lazily via
    :func:`add_run_secrets` from ``_prepare_runtime_components`` (which also
    covers subagents — they share the parent run task / secret set). ``extra``
    lets a caller fold in extra plaintext (e.g. a subagent's own creds into the
    parent set).
    """

    secrets: set[str] = set()
    if cfg.api_key:
        secrets |= collect_secret_values(cfg.api_key)
    if cfg.provider_api_keys:
        secrets |= collect_secret_values(cfg.provider_api_keys)
    collect_url_userinfo(cfg.base_url, secrets)
    for entry in cfg.model_fallback_chain or []:
        if isinstance(entry, Mapping):
            collect_url_userinfo(entry.get("base_url"), secrets)
    for tool_config in cfg.tools_config or []:
        if not isinstance(tool_config, Mapping):
            continue
        secrets |= collect_secret_values(tool_config.get("credentials"))
        secrets |= collect_secret_values(tool_config.get("mcp_transport_headers"))
        collect_url_userinfo(tool_config.get("url"), secrets)
    if extra is not None:
        secrets |= collect_secret_values(extra)
    return secrets


def _collect_into(obj: object, out: set[str]) -> None:
    if isinstance(obj, str):
        _add_str_leaf(obj, out)
        return
    if isinstance(obj, Mapping):
        # ``{name/key/header: <meta>, value: <secret>}`` — collect only the
        # value so the metadata field (e.g. the header name) isn't masked.
        if "value" in obj and any(meta in obj for meta in _PAIR_META_KEYS):
            _collect_into(obj["value"], out)
            return
        for value in obj.values():
            _collect_into(value, out)
        return
    # Strings are handled above; bytes/bytearray are not secrets we mask.
    if isinstance(obj, Sequence) and not isinstance(obj, (bytes, bytearray)):
        for item in obj:
            _collect_into(item, out)
        return
    if isinstance(obj, (set, frozenset)):
        for item in obj:
            _collect_into(item, out)
        return


def _add_str_leaf(value: str, out: set[str]) -> None:
    if len(value) >= _MIN_SECRET_LEN:
        out.add(value)
    for prefix in _AUTH_SCHEME_PREFIXES:
        if value.startswith(prefix):
            body = value[len(prefix) :].strip()
            if len(body) >= _MIN_SECRET_LEN:
                out.add(body)


__all__ = [
    "add_run_secrets",
    "collect_cfg_secret_values",
    "collect_secret_values",
    "collect_url_userinfo",
    "get_run_secrets",
    "reset_run_secrets",
    "set_run_secrets",
]
