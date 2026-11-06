"""Install profiles: which components an installation is expected to run (plan §11, D49).

A profile only sets expectations for the operator's system status; it switches nothing on
or off. CPU, GPU, RAM and storage budgets per profile are decided after measurement (D49).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

ProfileKey = Literal["minimal", "external_api", "gpu", "kubernetes", "airgapped"]
ComponentKey = Literal[
    "database",
    "pgvector",
    "worker",
    "object_store",
    "mcp_runtime",
    "pii_detector",
    "skill_sandbox",
]
Requirement = Literal["required", "recommended", "optional"]

COMPONENTS: Final[tuple[ComponentKey, ...]] = (
    "database",
    "pgvector",
    "worker",
    "object_store",
    "mcp_runtime",
    "pii_detector",
    "skill_sandbox",
)


@dataclass(frozen=True, slots=True)
class Profile:
    key: ProfileKey
    requirements: dict[ComponentKey, Requirement]
    # The object store must be S3-compatible (several servers share files).
    shared_storage: bool = False


def _profile(
    key: ProfileKey,
    *,
    required: tuple[ComponentKey, ...] = (),
    recommended: tuple[ComponentKey, ...] = (),
    shared_storage: bool = False,
) -> Profile:
    base: tuple[ComponentKey, ...] = ("database", "worker", "object_store")
    levels: dict[ComponentKey, Requirement] = {}
    for component in COMPONENTS:
        if component in base or component in required:
            levels[component] = "required"
        elif component in recommended:
            levels[component] = "recommended"
        else:
            levels[component] = "optional"
    return Profile(key, levels, shared_storage)


PROFILES: Final[dict[ProfileKey, Profile]] = {
    # One server, local volume storage (D16); everything beyond the core is opt-in.
    "minimal": _profile("minimal", recommended=("pgvector",)),
    # Models through provider APIs; Moldy-defined MCP servers run in the shared runtime.
    "external_api": _profile("external_api", recommended=("pgvector", "mcp_runtime")),
    # Models on the company's own GPUs; the Korean detector runs next to them.
    "gpu": _profile("gpu", recommended=("pgvector", "mcp_runtime", "pii_detector")),
    # Several replicas: shared S3 storage, the runtime and isolated skill runs are required.
    "kubernetes": _profile(
        "kubernetes",
        required=("pgvector", "mcp_runtime", "skill_sandbox"),
        shared_storage=True,
    ),
    # No outbound network: detection and skill runs stay inside.
    "airgapped": _profile(
        "airgapped",
        required=("pgvector",),
        recommended=("mcp_runtime", "pii_detector", "skill_sandbox"),
    ),
}
