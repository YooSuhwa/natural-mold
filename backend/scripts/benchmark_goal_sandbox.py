"""Compare two skill calls that pass a generated document through an intermediate file.

From backend: python -m scripts.benchmark_goal_sandbox measure --out measurements.json
Select --provider opensandbox to create resources using SKILL_SANDBOX_* settings.
With the filesystem provider, timings describe local copies and subprocesses only.

The optional --skill-dir package must be reviewed for external transfer. Its entrypoint
receives ("edit" or "verify", output_directory). Edit reads source.txt and writes
reviewed.txt with |draft| replaced by |final|. Verify reads the restored reviewed.txt
and writes its lowercase hexadecimal SHA-256 digest to reviewed.sha256 with no newline.
Every stage transfers the exact same skill package. Only new user outputs are collected.

An operator can reconcile an invoice or an allocated infrastructure cost record later.
Run the "assess" subcommand with the measurement and a JSON billing record containing
run_id, currency (three letters), evidence_ref, and totals_by_sandbox (one decimal
amount per measured resource). Include idle, storage, transfer, and cleanup charges.
An undocumented cost leaves the comparison pending. Use --retained-first for a second
run so order-dependent cold starts remain visible.
"""

# ruff: noqa: SLF001 -- measure the exact transfer and execution calls used by the product.
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from opensandbox import Sandbox


SCRIPT = b"""import hashlib
import sys
from pathlib import Path

outputs = Path(sys.argv[2])
document = outputs / "reviewed.txt"
if sys.argv[1] == "edit":
    source = (outputs / "source.txt").read_bytes()
    document.write_bytes(source.replace(b"|draft|", b"|final|"))
else:
    digest = hashlib.sha256(document.read_bytes()).hexdigest()
    (outputs / "reviewed.sha256").write_text(digest, encoding="ascii")
"""
STAGES = (("edit", "reviewed.txt"), ("verify", "reviewed.sha256"))
Provider = Literal["filesystem", "opensandbox"]


@dataclass
class Arm:
    mode: str
    sandbox_ids: list[str] = field(default_factory=list)
    uploaded_bytes: int = 0
    downloaded_bytes: int = 0
    preparation_seconds: float = 0
    pause_seconds: float = 0
    resume_seconds: float = 0
    total_seconds: float = 0
    intermediate_sha256: str = ""
    result_sha256: str = ""


def document(size: int) -> bytes:
    if not 1 <= size <= 32 * 1024**2:
        raise ValueError("Choose a document between 1 byte and 32 MiB.")
    line = "공개 시험 자료|draft|매출·지출·검토 의견\n".encode()
    return (line * (size // len(line) + 1))[:size]


class FilesystemWorkspace:
    """Use an actual disposable directory to exercise identical transfers without a server."""

    def __init__(self, root: Path):
        self.root = root
        self.skill = root / "skill"
        self.outputs = root / "outputs"

    async def open(self) -> str:
        self.skill.mkdir(parents=True)
        self.outputs.mkdir()
        return self.root.name

    async def transfer(self, skill: dict[str, bytes], previous: dict[str, bytes]) -> None:
        for path, files in [(self.skill, skill), (self.outputs, previous)]:
            for name, content in files.items():
                target = path / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)

    async def execute(
        self, entrypoint: str, label: str, expected_file: str, destination: Path
    ) -> None:
        process = await asyncio.create_subprocess_exec(
            sys.executable, entrypoint, label, str(self.outputs), cwd=self.skill, env={}
        )
        if await process.wait():
            raise RuntimeError("The filesystem benchmark command failed.")
        (destination / expected_file).write_bytes((self.outputs / expected_file).read_bytes())

    async def idle(self, seconds: float) -> tuple[float, float]:
        await asyncio.sleep(seconds)
        return 0, 0

    async def discard(self) -> None:
        import shutil

        shutil.rmtree(self.root)


class OpenSandboxWorkspace:
    """Use Moldy's production SDK operations with denied egress and empty credentials."""

    def __init__(self, idle_seconds: float):
        self.timeout = max(60.0, idle_seconds + 60.0)
        self.sandbox: Sandbox | None = None

    def handle(self) -> Sandbox:
        if self.sandbox is None:
            raise RuntimeError("No active sandbox for benchmark.")
        return self.sandbox

    async def open(self) -> str:
        from app.agent_runtime import skill_sandbox

        self.sandbox = await skill_sandbox._create(self.timeout, ())
        return self.sandbox.id

    async def transfer(self, skill: dict[str, bytes], previous: dict[str, bytes]) -> None:
        from app.agent_runtime import skill_sandbox

        await skill_sandbox._upload(self.handle(), list(skill.items()), list(previous.items()))

    async def execute(
        self, entrypoint: str, label: str, expected_file: str, destination: Path
    ) -> None:
        from app.agent_runtime import skill_sandbox

        run = await skill_sandbox.run_command(
            self.handle(), [sys.executable, entrypoint, label, skill_sandbox.OUTPUT_DIR], {}, 60
        )
        if run.returncode != 0 or run.timed_out:
            raise RuntimeError("The OpenSandbox benchmark command failed.")
        await skill_sandbox._download(self.handle(), destination)
        if not (destination / expected_file).is_file():
            raise RuntimeError(f"OpenSandbox did not return the expected {expected_file}.")

    async def idle(self, seconds: float) -> tuple[float, float]:
        from opensandbox import Sandbox
        from opensandbox.config import ConnectionConfig

        from app.config import settings

        current = self.handle()
        started = time.perf_counter()
        await current.pause()
        pause_seconds = time.perf_counter() - started
        await asyncio.sleep(seconds)
        started = time.perf_counter()
        # The endpoint and the SDK-owned transport may change on resume.
        connection = ConnectionConfig(
            api_key=settings.skill_sandbox_api_key or None,
            domain=settings.skill_sandbox_domain,
            use_server_proxy=settings.skill_sandbox_use_server_proxy,
            request_timeout=timedelta(seconds=self.timeout + 60),
        )
        self.sandbox = await Sandbox.resume(current.id, connection_config=connection)
        await current.close()
        return pause_seconds, time.perf_counter() - started

    async def discard(self) -> None:
        if self.sandbox is None:
            return
        try:
            await self.sandbox.kill()
        finally:
            await self.sandbox.close()
            self.sandbox = None


async def measure_arm(
    provider: Provider,
    root: Path,
    source: bytes,
    skill_files: dict[str, bytes],
    entrypoint: str,
    *,
    retain: bool,
    idle_seconds: float,
) -> Arm:
    result = Arm("retained" if retain else "per_call")
    await asyncio.to_thread(root.mkdir)
    started = time.perf_counter()
    active: FilesystemWorkspace | OpenSandboxWorkspace | None = None
    previous: dict[str, bytes] = {"source.txt": source}
    try:
        for stage_no, (label, filename) in enumerate(STAGES):
            prep = time.perf_counter()
            if active is None:
                active = (
                    FilesystemWorkspace(root / f"workspace-{stage_no}")
                    if provider == "filesystem"
                    else OpenSandboxWorkspace(idle_seconds)
                )
                result.sandbox_ids.append(await active.open())
            reload_files = previous if stage_no == 0 or not retain else {}
            await active.transfer(skill_files, reload_files)
            result.uploaded_bytes += sum(map(len, skill_files.values())) + sum(
                map(len, reload_files.values())
            )
            result.preparation_seconds += time.perf_counter() - prep
            downloaded = root / f"received-{stage_no}"
            downloaded.mkdir()
            await active.execute(entrypoint, label, filename, downloaded)
            receipts = {file.name: file.read_bytes() for file in downloaded.iterdir()}
            result.downloaded_bytes += sum(map(len, receipts.values()))
            previous.update(receipts)
            if not retain:
                await active.discard()
                active = None
            if stage_no == 0:
                if active is None:
                    await asyncio.sleep(idle_seconds)
                else:
                    result.pause_seconds, result.resume_seconds = await active.idle(idle_seconds)
                    result.preparation_seconds += result.resume_seconds
    finally:
        if active is not None:
            await asyncio.shield(active.discard())
    result.total_seconds = time.perf_counter() - started
    result.intermediate_sha256 = hashlib.sha256(previous["reviewed.txt"]).hexdigest()
    result.result_sha256 = previous["reviewed.sha256"].decode("ascii")
    expected = hashlib.sha256(source.replace(b"|draft|", b"|final|")).hexdigest()
    if result.intermediate_sha256 != expected or result.result_sha256 != expected:
        raise RuntimeError("The restored intermediate or final file failed digest verification.")
    return result


def provider_profile(provider: Provider) -> dict:
    if provider == "filesystem":
        return {"provider": "filesystem"}
    from app.config import settings

    domain = settings.skill_sandbox_domain
    if any(character in domain for character in "@?#"):
        raise ValueError("Configure a sandbox domain without user information, query or fragment.")
    return {
        "domain": domain,
        "image": settings.skill_sandbox_image,
        "cpu": settings.skill_sandbox_cpu,
        "memory": settings.skill_sandbox_memory,
        "network_domains": [],
    }


def packaged_workload(
    root: Path, skill_dir: Path | None, entrypoint: str
) -> tuple[dict[str, bytes], str]:
    from app.skills.package_hash import compute_package_tree_hash, is_excluded_package_path

    root.mkdir()
    if skill_dir is None:
        (root / "stage.py").write_bytes(SCRIPT)
    else:
        if not skill_dir.is_dir() or skill_dir.is_symlink():
            raise ValueError("Choose a versioned skill directory.")
        total = 0
        for path in skill_dir.rglob("*"):
            rel = path.relative_to(skill_dir)
            if is_excluded_package_path(rel):
                continue
            if path.is_symlink():
                raise ValueError("The benchmark package contains a symbolic link.")
            if not path.is_file():
                continue
            total += path.stat().st_size
            if total > 32 * 1024**2:
                raise ValueError("Choose a skill package under 32 MiB.")
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    files = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    if entrypoint not in files or not entrypoint.endswith(".py"):
        raise ValueError("The Python benchmark entrypoint must belong to the measured package.")
    return files, compute_package_tree_hash(root)


async def measure(
    provider: Provider,
    size: int,
    idle_seconds: float,
    *,
    retained_first: bool = False,
    skill_dir: Path | None = None,
    entrypoint: str = "stage.py",
) -> dict:
    if not math.isfinite(idle_seconds) or not 0 <= idle_seconds <= 3600:
        raise ValueError("Choose an idle interval from 0 to 3600 seconds.")
    if skill_dir is not None and provider != "opensandbox":
        raise ValueError(
            "Operator-supplied skills require the network-denied OpenSandbox provider."
        )
    source = document(size)
    report = {
        "schema_version": 1,
        "profile": provider_profile(provider),
        "run_id": str(uuid.uuid4()),
        "provider": provider,
        "input_bytes": size,
        "input_sha256": hashlib.sha256(source).hexdigest(),
        "idle_seconds": idle_seconds,
        "arms": {},
        "cost": None,
        "passes_time_and_cost": None,
    }
    with tempfile.TemporaryDirectory(prefix="moldy-goal-benchmark-") as temp:
        files, tree_hash = packaged_workload(Path(temp) / "package", skill_dir, entrypoint)
        report["workload"] = {
            "skill_tree_sha256": tree_hash,
            "script_sha256": hashlib.sha256(files[entrypoint]).hexdigest(),
            "entrypoint": entrypoint,
            "input_sha256": report["input_sha256"],
            "input_bytes": size,
        }
        for retain in (retained_first, not retained_first):
            arm = await measure_arm(
                provider,
                Path(temp) / str(retain),
                source,
                files,
                entrypoint,
                retain=retain,
                idle_seconds=idle_seconds,
            )
            report["arms"][arm.mode] = asdict(arm)
    return report


def assess(report: dict, invoice: dict) -> dict:
    """Link a reconciled total per sandbox (including idle, storage and transfer) to the run."""
    if report["provider"] != "opensandbox" or invoice["run_id"] != report["run_id"]:
        raise ValueError("Billing must match an OpenSandbox measurement run.")
    image = report.get("profile", {}).get("image", "")
    if report.get("schema_version") != 1 or not re.fullmatch(
        r"(?:[^@]+@)?sha256:[a-f0-9]{64}", image
    ):
        raise ValueError("Billing requires a version 1 measurement and an immutable image digest.")
    currency = invoice["currency"]
    if not re.fullmatch("[A-Z]{3}", currency) or not invoice["evidence_ref"].strip():
        raise ValueError("Billing requires a currency and evidence reference.")
    arms = report["arms"]
    if set(arms) != {"per_call", "retained"}:
        raise ValueError("Both measured arms are required.")
    hashes = {arm[key] for arm in arms.values() for key in ("intermediate_sha256", "result_sha256")}
    if len(hashes) != 1 or not re.fullmatch(r"[a-f0-9]{64}", next(iter(hashes))):
        raise ValueError("The recovered document must match across both arms and stages.")
    if any(
        not math.isfinite(arm["total_seconds"]) or arm["total_seconds"] <= 0
        for arm in arms.values()
    ):
        raise ValueError("Both measured times must be positive.")
    if len(arms["per_call"]["sandbox_ids"]) != 2 or len(arms["retained"]["sandbox_ids"]) != 1:
        raise ValueError("Require two ephemeral sandboxes and one retained sandbox.")
    expected_ids = [rid for arm in arms.values() for rid in arm["sandbox_ids"]]
    amounts = invoice["totals_by_sandbox"]
    if set(expected_ids) != set(amounts) or len(set(expected_ids)) != len(expected_ids):
        raise ValueError("Billing must cover exactly the measured sandbox IDs.")
    try:
        parsed = {rid: Decimal(str(amounts[rid])) for rid in expected_ids}
    except InvalidOperation as exc:
        raise ValueError("Invalid amount in billing records.") from exc
    if any(not amount.is_finite() or amount < 0 for amount in parsed.values()):
        raise ValueError("Billing totals must be finite and nonnegative.")
    costs = {name: sum(parsed[rid] for rid in arm["sandbox_ids"]) for name, arm in arms.items()}
    base_time = arms["per_call"]["total_seconds"]
    keep_time = arms["retained"]["total_seconds"]
    less_cost = costs["retained"] < costs["per_call"]
    allowed = (
        keep_time <= base_time
        and costs["retained"] <= costs["per_call"]
        and (keep_time < base_time or less_cost)
    )
    return {
        **report,
        "cost": {
            "currency": currency,
            "evidence_ref": invoice["evidence_ref"],
            "totals": {name: str(total) for name, total in costs.items()},
        },
        "passes_time_and_cost": allowed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("measure")
    run.add_argument("--provider", choices=("filesystem", "opensandbox"), default="filesystem")
    run.add_argument("--size-mib", type=int, default=4)
    run.add_argument("--idle-seconds", type=float, default=1)
    run.add_argument("--retained-first", action="store_true")
    run.add_argument(
        "--skill-dir", type=Path, help="Operator-reviewed secret-free package. OpenSandbox only."
    )
    run.add_argument(
        "--entrypoint",
        default="stage.py",
        help="Relative Python file taking edit|verify and an output directory.",
    )
    run.add_argument("--out", required=True, type=Path)
    bill = commands.add_parser("assess")
    bill.add_argument("--measurement", required=True, type=Path)
    bill.add_argument("--billing", required=True, type=Path)
    bill.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "measure":
        report = asyncio.run(
            measure(
                args.provider,
                args.size_mib * 1024**2,
                args.idle_seconds,
                retained_first=args.retained_first,
                skill_dir=args.skill_dir,
                entrypoint=args.entrypoint,
            )
        )
    else:
        report = assess(
            json.loads(args.measurement.read_text()), json.loads(args.billing.read_text())
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
