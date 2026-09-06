"""External residue-probe tests for cleanup discovery."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from types import TracebackType

import pytest

from tests.cleanup_discovery_support import (
    ManifestValidationError,
    discovery,
    postgres_payload,
    write_payload,
)
from tests.cleanup_discovery_support import (
    absent_probes_fixture as _absent_probes_fixture,  # noqa: F401
)


def test_empty_root_still_runs_global_probes(
    tmp_path: Path, absent_probes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an empty evidence root and one occupied fixed E2E port.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(discovery, "_port_has_listener", lambda port: port == 8101)

    # When/Then: the global probe detects residue despite no receipts.
    with pytest.raises(ManifestValidationError, match=r"^live_port$"):
        discovery.discover_and_probe(root)


def test_discovery_fails_closed_on_probe_error(
    tmp_path: Path, absent_probes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: Docker cannot execute its global label probe.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)

    def fail(_arguments: tuple[str, ...], *, timeout: float) -> subprocess.CompletedProcess[str]:
        del timeout
        raise subprocess.TimeoutExpired("docker", 1)

    monkeypatch.setattr(discovery, "probe_docker", fail)

    # When/Then: operational uncertainty is a cleanup-gate failure.
    with pytest.raises(ManifestValidationError, match=r"^probe_error$"):
        discovery.discover_and_probe(root)


def test_discovery_bounds_all_examined_temp_entries(
    tmp_path: Path, absent_probes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: unrelated temp entries exceed a deliberately narrowed global scan budget.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    for index in range(3):
        (absent_probes / f"unrelated-{index}").mkdir(mode=0o700)
    monkeypatch.setattr(discovery, "_MAX_TEMP_ENTRIES", 2, raising=False)

    # When/Then: the global root probe fails closed before an unbounded scan.
    with pytest.raises(ManifestValidationError, match=r"^probe_error$"):
        discovery.discover_and_probe(root)


def test_discovery_uses_one_cumulative_temp_scan_budget(
    tmp_path: Path, absent_probes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: entries are split across current and legacy temp parents.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    legacy = tmp_path / "legacy-temp"
    legacy.mkdir(mode=0o700)
    monkeypatch.setattr(discovery, "LEGACY_TEMP_PARENTS", (legacy,))
    for parent in (absent_probes, legacy):
        (parent / "unrelated").mkdir(mode=0o700)
    monkeypatch.setattr(discovery, "_MAX_TEMP_ENTRIES", 2)

    # When/Then: before/after scans and all parents share one total budget.
    with pytest.raises(ManifestValidationError, match=r"^probe_error$"):
        discovery.discover_and_probe(root)


def test_discovery_rejects_external_probe_workload_before_launch(
    tmp_path: Path, absent_probes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: one valid receipt exceeds a deliberately narrowed external-work budget.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    write_payload(root, "postgres.json", postgres_payload(absent_probes))
    calls: list[tuple[str, ...]] = []

    def unexpected_docker_probe(
        arguments: tuple[str, ...], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(discovery, "_MAX_EXTERNAL_PROBES", 1, raising=False)
    monkeypatch.setattr(discovery, "probe_docker", unexpected_docker_probe)

    # When/Then: the gate rejects the workload before launching a subprocess.
    with pytest.raises(ManifestValidationError, match=r"^probe_error$"):
        discovery.discover_and_probe(root)
    assert calls == []


def test_discovery_uses_one_cumulative_external_probe_deadline(
    tmp_path: Path, absent_probes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the clean global Docker probe consumes the entire external-probe deadline.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    write_payload(root, "postgres.json", postgres_payload(absent_probes))
    clock = [0.0]
    calls: list[tuple[tuple[str, ...], float]] = []

    def slow_probe(
        arguments: tuple[str, ...], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        calls.append((arguments, timeout))
        clock[0] = 2.0
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(discovery, "_PROBE_DEADLINE_SECONDS", 1.0, raising=False)
    monkeypatch.setattr(discovery.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(discovery, "probe_docker", slow_probe)

    # When/Then: no recorded-identity probe starts after the shared deadline expires.
    with pytest.raises(ManifestValidationError, match=r"^probe_error$"):
        discovery.discover_and_probe(root)
    assert len(calls) == 1
    assert calls[0][1] == 1.0


def test_discovery_fails_closed_on_recorded_process_probe_error(
    tmp_path: Path, absent_probes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a recorded process probe exits with an operational error, not "not found".
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    write_payload(root, "postgres.json", postgres_payload(absent_probes))

    def probe(argv: tuple[str, ...], _timeout: float) -> subprocess.CompletedProcess[str]:
        if argv[0] == "/bin/ps":
            return subprocess.CompletedProcess(argv, 2, "", "process probe failed")
        return subprocess.CompletedProcess(
            argv,
            0 if "--filter" in argv else 1,
            "",
            "Error: No such object: absent",
        )

    monkeypatch.setattr(discovery, "_run_probe", probe)

    # When/Then: probe uncertainty cannot be interpreted as process absence.
    with pytest.raises(ManifestValidationError, match=r"^probe_error$"):
        discovery.discover_and_probe(root)


def test_discovery_detects_recorded_process_identity(
    tmp_path: Path, absent_probes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a PostgreSQL receipt's exact recorded process is still alive.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    payload = postgres_payload(absent_probes)
    scenarios = payload["scenarios"]
    assert isinstance(scenarios, list)
    scenario = scenarios[0]
    assert isinstance(scenario, dict)
    live_identity = "Thu Sep  5 10:00:00 2026 runner\n"
    scenario["process_identity_sha256"] = hashlib.sha256(live_identity.encode()).hexdigest()
    write_payload(root, "postgres.json", payload)

    def probe(argv: tuple[str, ...], _timeout: float) -> subprocess.CompletedProcess[str]:
        if argv[0] == "/bin/ps":
            return subprocess.CompletedProcess(argv, 0, live_identity, "")
        return subprocess.CompletedProcess(
            argv,
            0 if "--filter" in argv else 1,
            "",
            "Error: No such object: absent",
        )

    monkeypatch.setattr(discovery, "_run_probe", probe)

    # When/Then: PID plus identity hash residue is rejected.
    with pytest.raises(ManifestValidationError, match=r"^live_process$"):
        discovery.discover_and_probe(root)


def test_discovery_probes_recorded_postgres_port(
    tmp_path: Path, absent_probes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a receipt records a non-fixed PostgreSQL port that is still listening.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    write_payload(root, "postgres.json", postgres_payload(absent_probes))
    monkeypatch.setattr(discovery, "_port_has_listener", lambda port: port == 49152)

    # When/Then: the receipt-derived port is independently probed and rejected.
    with pytest.raises(ManifestValidationError, match=r"^live_port$"):
        discovery.discover_and_probe(root)


def test_port_probe_checks_ipv6_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: IPv4 is clear while the same port has an IPv6-only loopback listener.
    families: list[int] = []

    class ProbeSocket:
        def __init__(self, family: int, _kind: int) -> None:
            self.family = family
            families.append(family)

        def __enter__(self) -> ProbeSocket:
            return self

        def __exit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc_value: BaseException | None,
            _traceback: TracebackType | None,
        ) -> None:
            return None

        def settimeout(self, _timeout: float) -> None:
            return None

        def connect_ex(self, _address: tuple[str, int]) -> int:
            return 0 if self.family == discovery.socket.AF_INET6 else 1

    monkeypatch.setattr(discovery.socket, "socket", ProbeSocket)

    # When/Then: both loopback families are checked and IPv6 residue is detected.
    assert discovery._port_has_listener(49152) is True
    assert families == [discovery.socket.AF_INET, discovery.socket.AF_INET6]


@pytest.mark.parametrize("residue", ["label", "root", "port"])
def test_discovery_detects_global_orphan_residue(
    tmp_path: Path,
    absent_probes: Path,
    monkeypatch: pytest.MonkeyPatch,
    residue: str,
) -> None:
    # Given: one global residue class exists independently of historical receipts.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    if residue == "root":
        (absent_probes / ".moldy-test-quarantine.orphan").mkdir(mode=0o700)
    elif residue == "port":
        monkeypatch.setattr(discovery, "_port_has_listener", lambda port: port == 3200)
    else:
        monkeypatch.setattr(
            discovery,
            "probe_docker",
            lambda arguments, *, timeout: subprocess.CompletedProcess(
                arguments, 0, "deadbeef\n", ""
            ),
        )

    # When/Then: the matching global probe fails closed.
    reason = {"label": "live_label", "root": "live_run_root", "port": "live_port"}[residue]
    with pytest.raises(ManifestValidationError, match=rf"^{reason}$"):
        discovery.discover_and_probe(root)
