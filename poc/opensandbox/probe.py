"""Escape probe run as a skill script.

Runs the same checks wherever it is spawned (the current host executor path or
an OpenSandbox sandbox) and prints one JSON object. It never prints secret
values: environment hits report only the variable name and value length.

Usage: python probe.py [--peer HOST:PORT ...] [--other-tenant-file PATH]
                       [--http-post URL JSON ...] [--after-tcp HOST:PORT ...]
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path

SECRET_NAMES = (
    "ENCRYPTION_KEYS",
    "JWT_SECRET",
    "DATABASE_URL",
    "OPENAI_API_KEY",
    "POC_FAKE_SECRET",
)


def _connect(host: str, port: int, timeout: float = 4.0) -> str:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "connected"
    except OSError as exc:
        return f"blocked: {type(exc).__name__}: {exc}"[:160]


def _resolve(host: str) -> str:
    try:
        return "resolved: " + socket.gethostbyname(host)
    except OSError as exc:
        return f"blocked: {type(exc).__name__}"


def _http_post(url: str, body: str) -> str:
    request = urllib.request.Request(
        url, data=body.encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=4.0) as response:
            return f"HTTP {response.status}: {response.read(600).decode(errors='replace')}"
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}: {exc.read(160).decode(errors='replace')}"
    except OSError as exc:
        return f"blocked: {type(exc).__name__}: {exc}"[:160]


def _secret_hits(raw: bytes) -> list[str]:
    hits = []
    for entry in raw.split(b"\0"):
        name, _, value = entry.partition(b"=")
        if name.decode(errors="replace") in SECRET_NAMES:
            hits.append(f"{name.decode()}(len={len(value)})")
    return hits


def _proc_environ_hits() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    proc = Path("/proc")
    if not proc.is_dir():
        return {"_": ["no /proc (not Linux)"]}
    for pid_dir in proc.iterdir():
        if not pid_dir.name.isdigit() or pid_dir.name == str(os.getpid()):
            continue
        try:
            hits = _secret_hits((pid_dir / "environ").read_bytes())
        except OSError:
            continue
        if hits:
            cmd = (pid_dir / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            found[f"pid {pid_dir.name} ({cmd[:60].strip()})"] = hits
    return found


def _cap_eff() -> str:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("CapEff:"):
                return line.split()[1]
    except OSError:
        pass
    return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--peer", action="append", default=[])
    parser.add_argument(
        "--http-post", nargs=2, action="append", default=[], metavar=("URL", "JSON")
    )
    parser.add_argument("--after-tcp", action="append", default=[])
    parser.add_argument(
        "--other-tenant-file", default="/app/data/tenants/tenant-b/conversations/c1/secret.txt"
    )
    args = parser.parse_args()

    other = Path(args.other_tenant_file)
    try:
        other_read = "READ: " + other.read_text()[:40]
    except OSError as exc:
        other_read = f"denied: {type(exc).__name__}"
    try:
        other.parent.mkdir(parents=True, exist_ok=True)
        (other.parent / "planted-by-probe.txt").write_text("x")
        other_write = "WROTE"
    except OSError as exc:
        other_write = f"denied: {type(exc).__name__}"

    report = {
        "uid": os.getuid(),
        "cap_eff": _cap_eff(),
        "own_env_secret_hits": [n for n in SECRET_NAMES if n in os.environ],
        "other_process_env_secret_hits": _proc_environ_hits(),
        "other_tenant_file_read": other_read,
        "other_tenant_file_write": other_write,
        "app_data_listing": sorted(p.name for p in Path("/app/data").iterdir())
        if Path("/app/data").is_dir()
        else "absent",
        "docker_socket": Path("/var/run/docker.sock").exists(),
        "dns_example_com": _resolve("example.com"),
        "tcp_example_com_443": _connect("example.com", 443),
        "tcp_pypi_org_443": _connect("pypi.org", 443),
        "tcp_direct_ip_1_1_1_1_443": _connect("1.1.1.1", 443),
        "tcp_metadata_169_254_169_254_80": _connect("169.254.169.254", 80, timeout=2.0),
        "tcp_host_docker_internal_18080": _connect("host.docker.internal", 18080, timeout=2.0),
        "peers": {
            peer: _connect(peer.rsplit(":", 1)[0], int(peer.rsplit(":", 1)[1]), timeout=2.0)
            for peer in args.peer
        },
        "http_posts": {url: _http_post(url, body) for url, body in args.http_post},
    }
    # Re-check egress after the POSTs, to see whether a policy rewrite from inside took effect.
    report["after_tcp"] = {
        t: _connect(t.rsplit(":", 1)[0], int(t.rsplit(":", 1)[1])) for t in args.after_tcp
    }
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
