"""Run the OpenSandbox isolation experiments against a local OpenSandbox server.

Usage (see README.md for the server setup):
    OSB_KEY_FILE=... python run_poc.py --label nft --out evidence/nft.json

Experiments:
  skill_run      real Moldy skill (patent-hwpx-generator) through MoldySandbox
  file_tools     deepagents BaseSandbox file tools over execute()
  escape_probe   probe.py inside sandbox A, aimed at sandbox B and the host
  resources      memory limit, pids limit, non-root execution
  timeout        command timeout kills the process
  interrupt      background command interrupted from the backend side
  execd_auth     can the host reach a sandbox's execd without credentials
  cleanup        kill() removes the containers
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
from datetime import timedelta
from pathlib import Path

from moldy_sandbox import MoldySandbox
from opensandbox.config import ConnectionConfigSync
from opensandbox.models.execd import RunCommandOpts
from opensandbox.models.sandboxes import NetworkPolicy, NetworkRule
from opensandbox.sync import SandboxSync

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SKILL_DIR = REPO / "backend/app/seed/system_skill_packages/patent-hwpx-generator"
IMAGE = "python:3.12-slim"
EXECD_PORT = 44772
EGRESS_POLICY_PORT = 18080

CONN = ConnectionConfigSync(
    api_key=Path(os.environ["OSB_KEY_FILE"]).read_text().strip(),
    domain="127.0.0.1:18080",
    request_timeout=timedelta(seconds=120),
)
DENY_ALL_BUT_PYPI = NetworkPolicy(
    default_action="deny", egress=[NetworkRule(action="allow", target="pypi.org")]
)


def _create(
    tenant: str,
    *,
    memory: str = "512Mi",
    env: dict[str, str] | None = None,
    secure_access: bool = False,
) -> SandboxSync:
    return SandboxSync.create(
        IMAGE,
        connection_config=CONN,
        network_policy=DENY_ALL_BUT_PYPI,
        resource={"cpu": "1", "memory": memory},
        env=env or {},
        metadata={"tenant": tenant, "poc": "moldy-skill-isolation"},
        timeout=timedelta(minutes=15),
        ready_timeout=timedelta(seconds=120),
        secure_access=secure_access,
    )


def _run(sb: SandboxSync, command: str, **opts) -> dict:
    t0 = time.monotonic()
    ex = sb.commands.run(command, opts=RunCommandOpts(**opts) if opts else None)
    return {
        "exit_code": ex.exit_code,
        "seconds": round(time.monotonic() - t0, 2),
        "stdout": "".join(m.text for m in ex.logs.stdout)[-8000:],
        "stderr": "".join(m.text for m in ex.logs.stderr)[-800:],
        "error": None if ex.error is None else f"{ex.error.name}: {ex.error.value}"[:300],
    }


def _containers(sandbox_id: str) -> list[dict]:
    rows = subprocess.run(
        ["docker", "ps", "-a", "--no-trunc", "--format", "{{json .}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    found = []
    for row in rows:
        info = json.loads(row)
        if sandbox_id in info.get("Labels", "") or sandbox_id in info.get("Names", ""):
            found.append({"name": info["Names"], "image": info["Image"], "state": info["State"]})
    return found


def _internal_ip(sandbox_id: str) -> str | None:
    for container in _containers(sandbox_id):
        ip = subprocess.run(
            [
                "docker",
                "inspect",
                "-f",
                "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                container["name"],
            ],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if ip:
            return ip
    return None


def _host_endpoint(sb: SandboxSync, port: int) -> str:
    return sb.get_endpoint(port).endpoint


def skill_run() -> dict:
    t0 = time.monotonic()
    sb = _create("tenant-a")
    created = round(time.monotonic() - t0, 2)
    try:
        backend = MoldySandbox(sb, workdir="/workspace")
        files = [
            (f"/workspace/skills/patent-hwpx-generator/{p.relative_to(SKILL_DIR)}", p.read_bytes())
            for p in sorted(SKILL_DIR.rglob("*"))
            if p.is_file()
        ]
        uploaded = backend.upload_files(files)
        t1 = time.monotonic()
        result = backend.execute(
            "cd /workspace/skills/patent-hwpx-generator && mkdir -p /workspace/outputs && "
            "OUTPUTS_DIR=/workspace/outputs python scripts/generate_hwpx.py "
            "--input examples/e2e-patent.json --output moldy-patent-demo.hwpx",
            timeout=60,
        )
        run_seconds = round(time.monotonic() - t1, 2)
        [download] = backend.download_files(["/workspace/outputs/moldy-patent-demo.hwpx"])
        hwpx = {"error": download.error}
        if download.content:
            with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
                hwpx = {
                    "bytes": len(download.content),
                    "mimetype": archive.read("mimetype").decode(),
                    "entries": len(archive.namelist()),
                    "crc_ok": archive.testzip() is None,
                }
        return {
            "create_seconds": created,
            "uploaded_files": len(uploaded),
            "upload_errors": [u.path for u in uploaded if u.error],
            "execute": {
                "exit_code": result.exit_code,
                "output": result.output[-600:],
                "seconds": run_seconds,
            },
            "downloaded_hwpx": hwpx,
        }
    finally:
        sb.kill()


def file_tools() -> dict:
    sb = _create("tenant-a")
    try:
        backend = MoldySandbox(sb)
        write = backend.write("/workspace/notes/plan.md", "line one\nmoldy sandbox line\n")
        edit = backend.edit("/workspace/notes/plan.md", "line one", "line 1")
        return {
            "write_error": getattr(write, "error", None),
            "edit_error": getattr(edit, "error", None),
            "read": str(backend.read("/workspace/notes/plan.md"))[:300],
            "ls": str(backend.ls("/workspace/notes"))[:300],
            "grep": str(backend.grep("moldy", path="/workspace"))[:300],
            "glob": str(backend.glob("**/*.md", path="/workspace"))[:300],
        }
    finally:
        sb.kill()


def escape_probe() -> dict:
    victim = _create("tenant-b", env={"POC_FAKE_SECRET": "tenant-b-secret"})
    attacker = _create("tenant-a", env={"POC_FAKE_SECRET": "tenant-a-own-credential"})
    try:
        victim_ip = _internal_ip(victim.id)
        victim_host_execd = _host_endpoint(victim, EXECD_PORT)
        # The endpoint is "<host>:<port>[/proxy/<port>]" (the path form when the server proxies).
        host_port, _, proxy_path = victim_host_execd.partition("/")
        victim_host_port = host_port.rsplit(":", 1)[-1]
        victim_host_url = f"http://host.docker.internal:{victim_host_port}" + (
            f"/{proxy_path}" if proxy_path else ""
        )
        attacker.files.write_file("/workspace/probe.py", (HERE / "probe.py").read_bytes())
        command_body = json.dumps(
            {"command": "grep -a -o POC_FAKE_SECRET=tenant-b /proc/1/environ"}
        )
        args = [
            f"--peer {victim_ip}:{EXECD_PORT}",
            f"--peer host.docker.internal:{victim_host_port}",
            f"--http-post http://{victim_ip}:{EXECD_PORT}/command '{command_body}'",
            f"--http-post {victim_host_url}/command '{command_body}'",
            f'--http-post http://127.0.0.1:{EGRESS_POLICY_PORT}/policy \'{{"defaultAction": "allow"}}\'',
            "--after-tcp example.com:443",
        ]
        result = _run(
            attacker, "python /workspace/probe.py " + " ".join(args), timeout=timedelta(seconds=90)
        )
        try:
            report = json.loads(result["stdout"])
        except json.JSONDecodeError:
            report = result
        tools = _run(
            attacker,
            "nft list ruleset 2>&1 | head -3; ip route 2>&1 | head -2; mount -t tmpfs none /mnt 2>&1 | head -1",
        )
        return {
            "victim_internal_ip": victim_ip,
            "victim_host_execd": victim_host_execd,
            "probe": report,
            "privileged_tools": tools["stdout"] + tools["stderr"],
        }
    finally:
        attacker.kill()
        victim.kill()


def _attempt(fn) -> object:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - PoC records the failure as data
        return {"exception": f"{type(exc).__name__}: {exc}"[:300]}


FORK_BOMB = (
    'python3 -c "import os\nn=0\ntry:\n  while True:\n    if os.fork()==0:\n'
    "      import time; time.sleep(30); os._exit(0)\n    n+=1\n"
    "except OSError as e:\n  print('stopped after', n, type(e).__name__)\""
)
ALLOC_1GB = "python3 -c \"b = bytearray(1024 * 1024 * 1024); print('allocated')\"; echo exit=$?"


def _stress(command: str) -> dict:
    """Run one stress command in a fresh 256Mi sandbox, then check execd still answers."""
    sb = _create("tenant-a", memory="256Mi")
    try:
        result = {"run": _attempt(lambda: _run(sb, command, timeout=timedelta(seconds=60)))}
        time.sleep(2)
        result["execd_alive_after"] = _attempt(lambda: _run(sb, "echo alive")["stdout"])
        result["oom_killed_processes"] = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.OOMKilled}}", f"sandbox-{sb.id}"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        return result
    finally:
        sb.kill()


def resources() -> dict:
    sb = _create("tenant-a", memory="256Mi")
    try:
        results = {
            "cgroup_limits": _attempt(
                lambda: _run(
                    sb,
                    "cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/pids.max /sys/fs/cgroup/cpu.max",
                )["stdout"]
            ),
            "non_root": _attempt(
                lambda: _run(sb, "id; touch /etc/x 2>&1 | head -1", uid=1000, gid=1000)
            ),
        }
    finally:
        sb.kill()
    results["alloc_1gb"] = _stress(ALLOC_1GB)
    results["fork_bomb"] = _stress(FORK_BOMB)
    return results


def timeout_check() -> dict:
    sb = _create("tenant-a")
    try:
        started = _run(sb, "sleep 300", timeout=timedelta(seconds=3))
        leftover = _run(
            sb,
            "for p in /proc/[0-9]*; do tr '\\0' ' ' < $p/cmdline; echo; done | grep -c '^sleep 300' || true",
        )
        return {"run": started, "sleep_processes_left": leftover["stdout"].strip()}
    finally:
        sb.kill()


def interrupt_check() -> dict:
    sb = _create("tenant-a")
    try:
        ex = sb.commands.run("sleep 600", opts=RunCommandOpts(background=True))
        time.sleep(1)
        before = _run(
            sb,
            "for p in /proc/[0-9]*; do tr '\\0' ' ' < $p/cmdline; echo; done | grep -c '^sleep 600' || true",
        )
        sb.commands.interrupt(ex.id)
        time.sleep(1)
        after = _run(
            sb,
            "for p in /proc/[0-9]*; do tr '\\0' ' ' < $p/cmdline; echo; done | grep -c '^sleep 600' || true",
        )
        return {"before": before["stdout"].strip(), "after_interrupt": after["stdout"].strip()}
    finally:
        sb.kill()


def _host_post(url: str, body: dict) -> str:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return f"HTTP {response.status}: {response.read(200).decode(errors='replace')}"
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}: {exc.read(200).decode(errors='replace')}"
    except OSError as exc:
        return f"error: {exc}"


def execd_auth() -> dict:
    results = {}
    for secure in (False, True):
        try:
            sb = _create("tenant-a", secure_access=secure)
        except Exception as exc:  # noqa: BLE001 - PoC records the failure as data
            results[f"secure_access={secure}"] = {
                "create_error": f"{type(exc).__name__}: {exc}"[:300]
            }
            continue
        try:
            endpoint = _host_endpoint(sb, EXECD_PORT)
            results[f"secure_access={secure}"] = {
                "endpoint": endpoint,
                "unauthenticated_post_command": _host_post(
                    f"http://{endpoint}/command", {"command": "id"}
                ),
            }
        finally:
            sb.kill()
    return results


def cleanup() -> dict:
    sb = _create("tenant-a")
    sandbox_id = sb.id
    before = _containers(sandbox_id)
    sb.kill()
    time.sleep(3)
    return {"containers_before": before, "containers_after_kill": _containers(sandbox_id)}


EXPERIMENTS = {
    "skill_run": skill_run,
    "file_tools": file_tools,
    "escape_probe": escape_probe,
    "resources": resources,
    "timeout": timeout_check,
    "interrupt": interrupt_check,
    "execd_auth": execd_auth,
    "cleanup": cleanup,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()
    results: dict[str, object] = {"label": args.label}
    for name, fn in EXPERIMENTS.items():
        if args.only and name not in args.only:
            continue
        t0 = time.monotonic()
        try:
            results[name] = fn()
        except Exception as exc:  # noqa: BLE001 - keep running the other experiments
            results[name] = {"exception": f"{type(exc).__name__}: {exc}"[:500]}
        print(f"{name}: {time.monotonic() - t0:.1f}s", flush=True)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
