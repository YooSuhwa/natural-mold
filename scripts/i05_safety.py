"""Target guards and config loading for the I-05 staging harnesses (plan 16.6).

Both harnesses only speak HTTP(S) to a deployed Moldy API (and, for the load test,
optionally to OpenFGA). They refuse database-style ports, credentials in URLs and
any non-local host unless the operator passes ``--i-know-this-is-staging``. Secrets
never live in the config file or on the command line: every password or token is
read from the environment variable the config names (``*_env`` keys).
"""

from __future__ import annotations

import ipaddress
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# Ports of stateful backends (PostgreSQL, PgBouncer, MySQL, Redis, Mongo, Kafka).
DB_PORTS = frozenset({5432, 5433, 5434, 5435, 6432, 3306, 6379, 27017, 9092})
LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain"})
SECRET_KEYS = frozenset({"password", "token", "api_key", "secret", "bearer", "cookie"})


class GuardError(RuntimeError):
    """The harness refuses to run (exit code 2)."""


def is_local_host(host: str) -> bool:
    if host in LOCAL_NAMES or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_target(url: str, *, staging_ack: bool, allow_http: bool = False) -> str:
    """Return the normalised base URL or raise ``GuardError``."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise GuardError(f"{url!r}: only http(s) API targets are allowed")
    host = parts.hostname or ""
    if not host:
        raise GuardError(f"{url!r}: missing host")
    if parts.username or parts.password:
        raise GuardError("credentials embedded in the URL are refused; use *_env variables")
    if parts.port in DB_PORTS:
        raise GuardError(f"port {parts.port} is a database/broker port; target the HTTP API")
    if parts.query or parts.fragment:
        raise GuardError(f"{url!r}: base URL must not carry a query or fragment")
    local = is_local_host(host)
    if not local and not staging_ack:
        raise GuardError(f"{host} is not local; pass --i-know-this-is-staging to run against it")
    if not local and parts.scheme != "https" and not allow_http:
        raise GuardError(f"{host} is not local; staging must be https (or pass --allow-http)")
    return url.rstrip("/")


def find_literal_secrets(node: Any, path: str = "$") -> list[str]:
    """JSON paths of secret-looking keys that hold a literal value instead of ``*_env``."""
    found: list[str] = []
    if isinstance(node, Mapping):
        for key, value in node.items():
            child = f"{path}.{key}"
            name = str(key).lower()
            exempt = name.endswith(("_env", "_id", "_ids"))
            secretish = not exempt and any(part in name for part in SECRET_KEYS)
            if secretish and isinstance(value, str) and value:
                found.append(child)
            found.extend(find_literal_secrets(value, child))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(find_literal_secrets(item, f"{path}[{index}]"))
    return found


@dataclass(frozen=True, slots=True)
class Persona:
    name: str
    email: str
    password_env: str
    org_id: str | None = None

    def password(self) -> str:
        value = os.environ.get(self.password_env)
        if not value:
            raise GuardError(f"persona {self.name}: set the {self.password_env} variable")
        return value


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    base_url: str
    personas: dict[str, Persona]
    sections: dict[str, Any] = field(default_factory=dict)

    def persona(self, name: str) -> Persona:
        if name not in self.personas:
            raise GuardError(f"config has no persona {name!r}")
        return self.personas[name]

    def section(self, name: str) -> dict[str, Any]:
        value = self.sections.get(name) or {}
        if not isinstance(value, dict):
            raise GuardError(f"config section {name!r} must be an object")
        return value

    def missing_env(self) -> list[str]:
        """Names (never values) of required secret variables that are unset."""
        names = [p.password_env for p in self.personas.values()]
        token_env = self.section("fga").get("token_env")
        if token_env:
            names.append(str(token_env))
        return sorted({name for name in names if not os.environ.get(name)})


def parse_config(raw: Mapping[str, Any], base_url: str | None = None) -> HarnessConfig:
    leaks = find_literal_secrets(raw)
    if leaks:
        raise GuardError(f"literal secrets in config (use *_env keys): {', '.join(leaks)}")
    url = base_url or raw.get("base_url")
    if not isinstance(url, str) or not url:
        raise GuardError("base_url is required (config or --base-url)")
    personas: dict[str, Persona] = {}
    for name, item in dict(raw.get("personas") or {}).items():
        if not isinstance(item, Mapping) or "email" not in item or "password_env" not in item:
            raise GuardError(f"persona {name!r} needs email and password_env")
        org = item.get("org_id")
        personas[name] = Persona(name, str(item["email"]), str(item["password_env"]), org)
    sections = {k: v for k, v in raw.items() if k not in ("base_url", "personas")}
    return HarnessConfig(url, personas, sections)


def load_config(path: Path, base_url: str | None = None) -> HarnessConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GuardError(f"cannot read config {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise GuardError("config must be a JSON object")
    return parse_config(raw, base_url)
