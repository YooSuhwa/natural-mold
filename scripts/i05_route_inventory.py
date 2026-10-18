"""Route inventory and IDOR/CSRF probe planning for I-05 (plan 8.3 policies, 16.6).

``python scripts/i05_route_inventory.py --out routes.json`` (with ``PYTHONPATH=backend``)
dumps every live route with its reviewed ``app.authz`` policy; the security probe reads
that file, so the staging runner never needs the backend environment. Planning is pure:
it turns inventory entries plus the operator's foreign/own fixture IDs into probes.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

PARAM = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::[a-z]+)?\}")
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Cookie-authenticated policy kinds; KEY (/v1 API keys), PUBLIC, SHARE_TOKEN, E2E_ONLY excluded.
SESSION_KINDS = frozenset(
    {"RES", "PRIVATE", "SELF", "CAP", "CANDIDATES", "GOVERNANCE", "SUPER", "CATALOG"}
)
IDOR_KINDS = frozenset({"RES", "PRIVATE", "SELF", "GOVERNANCE", "CAP"})
MISSING_ID = "00000000-0000-4000-8000-000000000105"  # exists nowhere
PLACEHOLDERS = {
    "file_path": "SKILL.md",
    "requirement_key": "i05-probe",
    "case_index": "0",
    "filename": "i05-probe.png",
    "tool_call_id": "i05-probe",
    "key": "i05-probe",
}


@dataclass(frozen=True, slots=True)
class RouteEntry:
    method: str
    path: str
    kind: str
    param: str | None = None
    type_: str | None = None


@dataclass(frozen=True, slots=True)
class Probe:
    method: str
    path: str
    template: str
    family: str
    foreign_params: tuple[str, ...]
    control_path: str


def path_params(path: str) -> list[str]:
    return PARAM.findall(path)


def family(path: str) -> str:
    """Route family: the static prefix before the first path parameter (max 3 segments)."""
    static: list[str] = []
    for segment in path.strip("/").split("/"):
        if segment.startswith("{"):
            break
        static.append(segment)
    return "/" + "/".join(static[:3])


def fill(template: str, values: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        return values[match.group(1)]

    return PARAM.sub(replace, template)


def _value(name: str, *maps: Mapping[str, str]) -> str:
    for values in maps:
        if values.get(name):
            return str(values[name])
    return PLACEHOLDERS.get(name, MISSING_ID)


def _random_ids(params: Iterable[str], seed: int) -> dict[str, str]:
    return {
        name: PLACEHOLDERS.get(name, str(uuid.UUID(int=(seed << 64) | index | (4 << 76))))
        for index, name in enumerate(params)
    }


def plan_idor(
    entries: Sequence[RouteEntry],
    foreign: Mapping[str, str],
    own: Mapping[str, str] | None = None,
    *,
    include_mutations: bool = False,
    include_delete: bool = False,
) -> tuple[list[Probe], list[dict[str, str]]]:
    """Probes for every route with path parameters; unprobed routes keep a reason.

    A probe puts at least one *foreign* (other company) ID in the path; the remaining
    parameters take the operator's own IDs (nested IDOR, e.g. own conversation +
    foreign artifact) or fixed placeholders. The control path swaps every ID for an
    ID that exists nowhere, so a correct gate answers both identically (404 parity).
    """
    own = own or {}
    probes: list[Probe] = []
    unprobed: list[dict[str, str]] = []
    for entry in entries:
        params = path_params(entry.path)
        if not params or entry.kind not in IDOR_KINDS:
            continue
        reason = _skip_reason(entry, include_mutations, include_delete)
        hit = [name for name in params if foreign.get(name)]
        if reason is None and not hit:
            reason = f"no foreign fixture for {params}"
        gate = entry.param
        if reason is None and gate in params and not (foreign.get(gate) or own.get(gate)):
            reason = f"policy parameter {gate} has no foreign/own fixture"
        if reason:
            unprobed.append({"route": f"{entry.method} {entry.path}", "reason": reason})
            continue
        values = {name: _value(name, foreign, own) for name in params}
        control = _random_ids(params, seed=len(probes) + 1)
        probes.append(
            Probe(
                entry.method,
                fill(entry.path, values),
                entry.path,
                family(entry.path),
                tuple(hit),
                fill(entry.path, control),
            )
        )
    return probes, unprobed


def _skip_reason(entry: RouteEntry, mutations: bool, delete: bool) -> str | None:
    if entry.method == "DELETE" and not delete:
        return "DELETE skipped (pass --include-delete)"
    if entry.method not in SAFE_METHODS and not mutations:
        return "mutation skipped (pass --include-mutations)"
    return None


def plan_csrf(entries: Sequence[RouteEntry], limit: int | None = None) -> list[Probe]:
    """Cookie-authenticated mutations with path IDs; all IDs are nonexistent (no side effect)."""
    probes: list[Probe] = []
    for entry in entries:
        params = path_params(entry.path)
        if entry.method in SAFE_METHODS or entry.kind not in SESSION_KINDS or not params:
            continue
        path = fill(entry.path, _random_ids(params, seed=0xC5))
        probes.append(Probe(entry.method, path, entry.path, family(entry.path), (), path))
    return probes[:limit] if limit else probes


def families(entries: Sequence[RouteEntry]) -> set[str]:
    return {family(e.path) for e in entries if path_params(e.path) and e.kind in IDOR_KINDS}


def load_inventory(path: Path) -> list[RouteEntry]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [RouteEntry(**row) for row in rows]


def dump_inventory() -> list[RouteEntry]:
    """Live routes of ``app.main.create_app`` (needs ``PYTHONPATH=backend``)."""
    from fastapi.routing import APIRoute

    policy_of = importlib.import_module("app.authz.policies").policy_of
    create_app = importlib.import_module("app.main").create_app
    entries: list[RouteEntry] = []
    for route in create_app().routes:
        if not isinstance(route, APIRoute):
            continue
        policy = policy_of(route.endpoint)
        kind = getattr(policy, "kind", "NONE")
        if kind == "E2E_ONLY":  # test helpers never ship to staging
            continue
        for method in sorted(route.methods):
            entries.append(
                RouteEntry(
                    method,
                    route.path,
                    kind,
                    getattr(policy, "param", None),
                    getattr(policy, "type_", None),
                )
            )
    return entries


def main() -> int:  # pragma: no cover - CLI wrapper
    parser = argparse.ArgumentParser(description="dump the authz route inventory as JSON")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows = [asdict(entry) for entry in dump_inventory()]
    args.out.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} routes to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
