"""Start both ordinary chat replicas and release their ports together."""

from __future__ import annotations

import sys
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path

from processes import managed, wait_ready


@contextmanager
def worker_pair(
    backend: Path,
    logs: Path,
    env: dict[str, str],
    ports_by_label: Mapping[str, int],
    record: dict,
    *,
    prefix: str = "",
) -> Iterator[None]:
    """Serve a and b under the app DB role; unwind every owned group on phase exit."""
    if set(ports_by_label) != {"a", "b"} or len(set(ports_by_label.values())) != 2:
        raise ValueError(
            "The two acceptance replicas require distinct ports for a and b"
        )
    tag = f"{prefix.rstrip('-')}-" if prefix else ""
    runtime_env = {
        key: value
        for key, value in env.items()
        if key
        not in {
            "MIGRATION_DATABASE_URL",
            "MOLDY_AGUI_OBSERVER_URL",
            "MOLDY_AGUI_OBSERVER",
        }
    }
    process_ids = record["processes"]
    with ExitStack() as processes:
        for label in ("a", "b"):
            port = ports_by_label[label]
            api_name = f"{tag}api-{label}"
            job_name = f"{tag}jobs-{label}"
            if api_name in process_ids or job_name in process_ids:
                raise ValueError(
                    f"Acceptance worker names already recorded for {api_name}"
                )
            api, api_log = processes.enter_context(
                managed(
                    api_name,
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "app.main:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                    ],
                    backend,
                    runtime_env,
                    logs,
                )
            )
            process_ids[api_name] = api.pid
            wait_ready(
                api,
                api_log,
                runtime_env,
                url=f"http://localhost:{port}/api/health",
                timeout=150,
            )
            worker, job_log = processes.enter_context(
                managed(
                    job_name,
                    [sys.executable, "-m", "app.jobs.worker"],
                    backend,
                    runtime_env,
                    logs,
                )
            )
            process_ids[job_name] = worker.pid
            wait_ready(worker, job_log, runtime_env, marker="started for")
        yield
