"""Owned live-egress proxy lifecycle and redacted receipt parsing."""

from __future__ import annotations

import json
import secrets
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from e2e_runner_process import start_owned_process, stop_process
from e2e_runner_runtime import FRONTEND_ROOT, E2eResources
from postgres_manifest_io import RunnerInterrupted


@dataclass(slots=True)
class ProxyProcess:
    process: subprocess.Popen[bytes]
    log_file: IO[bytes]
    base_url: str
    token: str
    receipt_path: Path


class ProxyStartError(RuntimeError):
    """Proxy acquisition failed and records whether its child was reaped."""

    def __init__(
        self, reason: str, process_group_stopped: bool, signal_number: int | None = None
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.process_group_stopped = process_group_stopped
        self.signal_number = signal_number


def start_live_proxy(resources: E2eResources, inherited: dict[str, str]) -> ProxyProcess:
    required = ("E2E_LLM_BASE_URL", "E2E_LLM_API_KEY", "E2E_LLM_MODEL")
    if any(not inherited.get(name, "").strip() for name in required):
        raise RuntimeError("live_llm_configuration_missing")
    ready = resources.run_root / "proxy-ready.json"
    receipt = resources.run_root / "proxy-receipt.json"
    token = secrets.token_urlsafe(32)
    env = {key: value for key, value in inherited.items() if key in {"PATH", "HOME"}}
    if inherited.get("E2E_EGRESS_ALLOW_OWNED_LOOPBACK") == "1":
        env["E2E_EGRESS_ALLOW_OWNED_LOOPBACK"] = "1"
    env.update(
        {
            "E2E_EGRESS_UPSTREAM_BASE_URL": inherited["E2E_LLM_BASE_URL"],
            "E2E_EGRESS_UPSTREAM_API_KEY": inherited["E2E_LLM_API_KEY"],
            "E2E_EGRESS_PROXY_TOKEN": token,
            "E2E_EGRESS_READY_FILE": str(ready),
            "E2E_EGRESS_RECEIPT_FILE": str(receipt),
            "E2E_EGRESS_PORT": "0",
        }
    )
    process, log_file = start_owned_process(
        ["node", str(FRONTEND_ROOT / "scripts/e2e-egress-guard.mjs")],
        cwd=FRONTEND_ROOT,
        env=env,
        log_path=resources.run_root / "proxy.log",
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not ready.exists() and process.poll() is None:
            time.sleep(0.02)
        decoded = json.loads(ready.read_text())
        base_url = decoded["proxyBaseUrl"]
        if not isinstance(base_url, str) or not base_url.startswith("http://127.0.0.1:"):
            raise RuntimeError("egress_proxy_unsafe_ready")
    except BaseException as error:
        stopped = stop_process(process)
        log_file.close()
        with suppress(OSError):
            ready.unlink()
        with suppress(OSError):
            receipt.unlink()
        reason = str(error) if isinstance(error, RuntimeError) else "egress_proxy_not_ready"
        signal_number = error.signal_number if isinstance(error, RunnerInterrupted) else None
        raise ProxyStartError(reason, stopped, signal_number) from error
    return ProxyProcess(process, log_file, base_url, token, receipt)


def stop_live_proxy(proxy: ProxyProcess | None) -> bool:
    if proxy is None:
        return True
    stopped = stop_process(proxy.process)
    proxy.log_file.close()
    return stopped and proxy.receipt_path.is_file()


def read_proxy_receipt(proxy: ProxyProcess | None, clean_stop: bool) -> dict[str, object]:
    if proxy is None:
        return {"enabled": False, "clean_stop": clean_stop, "records": []}
    try:
        decoded = json.loads(proxy.receipt_path.read_text())
        records = decoded["records"]
    except (OSError, KeyError, json.JSONDecodeError, TypeError):
        return {"enabled": True, "clean_stop": False, "records": []}
    if not isinstance(records, list) or len(records) > 100:
        return {"enabled": True, "clean_stop": False, "records": []}
    sanitized: list[dict[str, str | int]] = []
    for record in records:
        if not isinstance(record, dict):
            return {"enabled": True, "clean_stop": False, "records": []}
        method = record.get("method")
        origin = record.get("origin")
        path_class = record.get("path_class")
        status = record.get("status")
        count = record.get("count")
        if (
            not isinstance(method, str)
            or len(method) > 16
            or not isinstance(origin, str)
            or len(origin) > 256
            or not isinstance(path_class, str)
            or len(path_class) > 64
            or not isinstance(status, int)
            or status < 100
            or status > 599
            or not isinstance(count, int)
            or count < 1
        ):
            return {"enabled": True, "clean_stop": False, "records": []}
        sanitized.append(
            {
                "method": method,
                "origin": origin,
                "path_class": path_class,
                "status": status,
                "count": count,
            }
        )
    return {"enabled": True, "clean_stop": clean_stop, "records": sanitized}
