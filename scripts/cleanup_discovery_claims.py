"""Typed lifecycle-claim parsing for cleanup discovery."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from e2e_cleanup_contract import PORTS, RUN_ID, require
from postgres_cleanup_checker import ManifestValidationError

MAX_CLAIMS: Final = 2_048
_HEX_64: Final = re.compile(r"[0-9a-f]{64}")
_PG_MODES: Final = frozenset(
    {
        "all",
        "migration-roundtrip",
        "stream-resume",
        "run-lifecycle+stream-resume",
        "self-test",
    }
)
_RUN_ROOT_PREFIXES: Final = (".moldy-test-run.", ".moldy-pg-run-")
type JSONValue = None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]


@dataclass(frozen=True, slots=True)
class Claims:
    processes: frozenset[tuple[int, str]]
    run_roots: frozenset[Path]
    ports: frozenset[int]


def _claim_e2e(payload: JSONObject) -> Claims:
    run_id = payload.get("run_id")
    lane = payload.get("lane")
    require(
        payload.get("schema_version") == 1
        and isinstance(run_id, str)
        and RUN_ID.fullmatch(run_id) is not None,
        "discovery_claim",
    )
    require(
        isinstance(lane, str)
        and lane in PORTS
        and (payload.get("frontend_port"), payload.get("backend_port")) == PORTS[lane],
        "discovery_claim",
    )
    return Claims(
        frozenset(),
        frozenset(),
        frozenset(),
    )


def _claim_postgres(payload: JSONObject, temp_parents: tuple[Path, ...]) -> Claims:
    mode = payload.get("mode")
    require(
        payload.get("schema_version") == 1 and isinstance(mode, str) and mode in _PG_MODES,
        "discovery_claim",
    )
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ManifestValidationError("discovery_claim")
    require(len(scenarios) <= MAX_CLAIMS, "discovery_limit")
    processes: set[tuple[int, str]] = set()
    roots: set[Path] = set()
    ports: set[int] = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise ManifestValidationError("discovery_claim")
        process_id = scenario.get("process_id")
        process_hash = scenario.get("process_identity_sha256")
        require(process_id is not None or process_hash is None, "discovery_claim")
        if process_id is not None:
            if (
                not isinstance(process_id, int)
                or isinstance(process_id, bool)
                or process_id <= 0
                or (
                    process_hash is not None
                    and (
                        not isinstance(process_hash, str) or _HEX_64.fullmatch(process_hash) is None
                    )
                )
            ):
                raise ManifestValidationError("discovery_claim")
            if isinstance(process_hash, str):
                processes.add((process_id, process_hash))
        root_created = scenario.get("run_root_created")
        require(root_created is None or isinstance(root_created, bool), "discovery_claim")
        raw_root = scenario.get("run_root")
        require(not (root_created is True and raw_root is None), "discovery_claim")
        if raw_root is not None:
            if not isinstance(raw_root, str):
                raise ManifestValidationError("discovery_claim")
            run_root = Path(raw_root)
            require(
                run_root.is_absolute()
                and run_root.parent.resolve(strict=False) in temp_parents
                and run_root.name.startswith(_RUN_ROOT_PREFIXES),
                "discovery_claim",
            )
            roots.add(run_root)
        port_observed = scenario.get("port_mapping_observed")
        require(port_observed is None or isinstance(port_observed, bool), "discovery_claim")
        port = scenario.get("port")
        require(not (port_observed is True and port is None), "discovery_claim")
        if port is not None:
            if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
                raise ManifestValidationError("discovery_claim")
            ports.add(port)
    return Claims(
        frozenset(processes),
        frozenset(roots),
        frozenset(ports),
    )


def merge_claims(claims: list[Claims]) -> Claims:
    """Merge claims and reject an oversized aggregate before live probes."""
    merged = Claims(
        frozenset(process for claim in claims for process in claim.processes),
        frozenset(root for claim in claims for root in claim.run_roots),
        frozenset(port for claim in claims for port in claim.ports),
    )
    groups = (
        merged.processes,
        merged.run_roots,
        merged.ports,
    )
    require(sum(len(group) for group in groups) <= MAX_CLAIMS, "discovery_limit")
    return merged


def classify_claim(payload: JSONObject, temp_parents: tuple[Path, ...]) -> Claims | None:
    """Return a supported lifecycle claim while ignoring ordinary JSON metadata."""
    if payload.get("runner") == "moldy-isolated-e2e":
        return _claim_e2e(payload)
    mode = payload.get("mode")
    if isinstance(mode, str) and mode in _PG_MODES:
        return _claim_postgres(payload, temp_parents)
    return None
