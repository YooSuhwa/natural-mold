"""Shared test-only contracts for canonical composite project gates."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Literal, Protocol, TypedDict

import pytest

type JSONValue = None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]
type NodeKind = Literal["isolated", "postgres", "e2e"]

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
EXPECTED_WAVE_1 = (
    "backend-full",
    "todo05-storage-security",
    "todo06-filesystem-security",
    "todo07-project-facts",
    "todo07-project-facts-check",
    "postgres-all",
    "frontend-lint",
    "frontend-i18n",
    "frontend-type-safety",
    "frontend-e2e-hygiene",
    "frontend-vitest",
    "frontend-build",
    "scripted-full",
)
EXPECTED_WAVE_2 = EXPECTED_WAVE_1[:-1] + (
    "todo08-runtime-contracts",
    "todo09-runtime-dependency-guard",
    "todo09-runtime-dependency-tests",
    "todo10-runtime-preparation-contracts",
    "todo11-streaming-contracts",
    "postgres-stream-resume",
    "todo12-frontend-contracts",
    "todo12-frontend-architecture",
    "todo13-runtime-split-contracts",
    "todo14-assistant-contracts",
    "todo14-visual-capture",
    "scripted-full",
)
EXPECTED_WAVE_3 = EXPECTED_WAVE_2[:-1] + (
    "todo15-runtime-policy-contracts",
    "postgres-migration-roundtrip",
    "todo16-policy-snapshot-contracts",
    "postgres-run-lifecycle-stream-resume",
    "todo17-policy-portability",
    "todo17-frontend-policy-portability",
    "scripted-full",
)
EXPECTED_WAVE_4 = EXPECTED_WAVE_3[:-1] + (
    "todo18-filesystem-policy-contracts",
    "todo19-todo-policy-contracts",
    "todo19-frontend-todo-contracts",
    "todo20-summarization-policy-contracts",
    "todo20-frontend-compaction-contracts",
    "todo21-runtime-settings-contracts",
    "todo21-runtime-policy-e2e",
    "todo21-visual-capture",
    "scripted-full",
)
EXPECTED_WAVE_5 = EXPECTED_WAVE_4[:-1] + (
    "todo22-frontend-quality",
    "todo23-governance-contracts",
    "todo23-backend-coverage",
    "todo23-frontend-coverage",
    "todo24-docs-contracts",
    "todo24-docs-check",
    "scripted-full",
)
EXPECTED_FINAL_STATIC = EXPECTED_WAVE_5[:-1] + (
    "final-backend-ruff",
    "final-changed-python-types",
    "final-changed-python-format",
    "final-frontend-a11y",
    "final-frontend-design",
    "final-frontend-architecture",
    "final-cleanup-discovery",
    "scripted-full",
)


class ReceiptSummaryPayload(TypedDict):
    relative_path: str
    sha256: str
    cleanup_passed: bool
    secret_scan_passed: bool | None
    workers: int | None
    retries: int | None
    screenshot_count: int | None


class RepositoryProvenanceLike(Protocol):
    base_sha: str
    head_sha: str


class TrustedToolchainLike(Protocol):
    python: Path
    node: Path
    pnpm: Path
    uv: Path
    home: Path
    user: str

    @property
    def path(self) -> str: ...


@dataclass(frozen=True, slots=True)
class SyntheticExecutableIdentity:
    """Structural executable identity for process-boundary unit tests."""

    path: Path
    link_device: int = 1
    link_inode: int = 2
    target: Path = Path("/usr/local/bin/docker")
    target_device: int = 3
    target_inode: int = 4
    target_size: int = 5
    target_mtime_ns: int = 6
    require_regular: bool = False
    target_ctime_ns: int = 7
    target_sha256: str = "a" * 64


def synthetic_docker_identity(
    path: Path = Path("/usr/local/bin/docker"),
) -> SyntheticExecutableIdentity:
    """Build a deterministic Docker identity without touching the host toolchain."""
    return SyntheticExecutableIdentity(path=path, target=path)


class GateNodeLike(Protocol):
    node_id: str
    kind: NodeKind
    cwd: Literal["backend", "frontend", "repo"]
    argv: tuple[str, ...]


class RunKwargs(TypedDict, total=False):
    cwd: Path
    env: dict[str, str]
    text: bool
    capture_output: bool
    check: bool


def load_module(name: str) -> ModuleType:
    """Load one production module lazily while preserving shared class identity."""
    scripts_path = str(SCRIPTS)
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def config_path(tmp_path: Path, nodes: list[str], *, wave: str = "wave-1") -> Path:
    """Write a complete canonical profile table with one selected composite mutation."""
    canonical_nodes = {
        "wave-1": list(EXPECTED_WAVE_1),
        "wave-2": list(EXPECTED_WAVE_2),
        "wave-3": list(EXPECTED_WAVE_3),
        "wave-4": list(EXPECTED_WAVE_4),
        "wave-5": list(EXPECTED_WAVE_5),
        "final-static": list(EXPECTED_FINAL_STATIC),
    }
    canonical_nodes[wave] = nodes
    path = tmp_path / "gates.json"
    profiles: JSONObject = {
        "smoke": {
            "lane": "scripted",
            "project": "scripted-smoke",
            "spec": "e2e/smoke.spec.ts",
            "workers": 1,
            "retries": 0,
        }
    }
    profiles.update(
        {
            name: {"kind": "composite", "nodes": profile_nodes}
            for name, profile_nodes in canonical_nodes.items()
        }
    )
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "profiles": profiles,
            }
        ),
        encoding="utf-8",
    )
    return path


def receipt_summary(path: Path) -> ReceiptSummaryPayload:
    return {
        "relative_path": path.name,
        "sha256": "a" * 64,
        "cleanup_passed": True,
        "secret_scan_passed": None,
        "workers": None,
        "retries": None,
        "screenshot_count": None,
    }


def composite_args(
    composite: ModuleType, tmp_path: Path
) -> tuple[RepositoryProvenanceLike, TrustedToolchainLike, dict[str, str]]:
    provenance = composite.RepositoryProvenance("a" * 40, "b" * 40)
    trusted = composite.TrustedToolchain(
        Path("/trusted/python"),
        Path("/trusted/node"),
        Path("/trusted/pnpm"),
        Path("/trusted/uv"),
        tmp_path,
        "tester",
        identities=(synthetic_docker_identity(),),
    )
    return (
        provenance,
        trusted,
        {
            "python": "3.12.9",
            "node": "22.1.0",
            "pnpm": "10.0.0",
        },
    )


def allow_stable_repository(composite: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(composite, "verify_provenance", lambda _expected, _root: None)
    monkeypatch.setattr(composite, "verify_toolchain", lambda _toolchain: None)
