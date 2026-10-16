"""Already-resolved inputs for prepared-run materialization (parent Task 10, I15).

``PreparationInputs`` is produced by the caller's ``prepare_inputs(db, run)``
resolver, which ``materialize_and_activate`` invokes only *after* it has
locked the queued run, inside the same gateway transaction, so every input is
read under the canonical locks (no check-then-use window). It carries: prompt,
converted messages, the lease model specs, selected tools with their trusted
origins, MCP configs, the declarative child closure with its stored tool
policy, the app-owned custom middleware slice, the frozen snapshot catalog and
run roots, and the non-secret digest material. ``MaterializerSeams`` carries the injectable
factories (lease model factory, MCP discovery, lease checkpointer, child
compiler) so tests can drive the installed compiler with scripted models.
Nothing here is persisted.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from deepagents.middleware.subagents import create_sub_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from app.agent_runtime import checkpointer as checkpointer_module
from app.agent_runtime.checkpoint_seed import CheckpointSeed, capture_checkpoint_seed
from app.agent_runtime.lease_bound_model import LeaseSendGuard
from app.agent_runtime.lease_checkpoint_namespace import LeaseNamespaceAuthority
from app.agent_runtime.lease_checkpoint_saver import LeaseCheckpointSaver
from app.agent_runtime.lease_model_factory import LeaseModelSpec, create_lease_chat_model
from app.agent_runtime.mcp_strict_loader import DiscoverTools, default_discover
from app.agent_runtime.runtime_config import PreparedRunLease
from app.agent_runtime.runtime_snapshot_catalog import RuntimeSnapshotCatalog
from app.agent_runtime.tool_access_policy import ToolOrigin
from app.config import settings

Closer = Callable[[], Awaitable[None]]


class OwnedModel(Protocol):
    """A lease-owned model (``LeaseOwnedModel``) and its single cleanup owner."""

    @property
    def model(self) -> BaseChatModel: ...

    async def aclose(self) -> None: ...


ModelFactory = Callable[[LeaseModelSpec, LeaseSendGuard], OwnedModel]


def default_model_factory(spec: LeaseModelSpec, guard: LeaseSendGuard) -> OwnedModel:
    return create_lease_chat_model(spec, guard=guard)


async def default_lease_checkpointer(
    *,
    thread_id: str,
    checkpoint_id: str | None,
    run_id: str,
    child_node_names: tuple[str, ...],
    delta_channels: tuple[str, ...],
) -> tuple[LeaseCheckpointSaver, CheckpointSeed]:
    """Capture the exact seed once (locks held) and bind a lineage-bound saver."""

    upstream = checkpointer_module.get_checkpointer()
    seed = await capture_checkpoint_seed(
        upstream,
        thread_id=thread_id,
        checkpoint_id=checkpoint_id,
        delta_channels=delta_channels,
        child_node_names=child_node_names,
    )
    saver = LeaseCheckpointSaver(
        upstream,
        seed=seed,
        run_id=run_id,
        namespaces=LeaseNamespaceAuthority(child_node_names),
    )
    return saver, seed


@dataclass(frozen=True, slots=True)
class PreparationInputs:
    thread_id: str
    system_prompt: str
    messages: tuple[Any, ...]
    model: LeaseModelSpec
    snapshot_catalog: RuntimeSnapshotCatalog
    run_root: Path
    writable_roots: Mapping[str, Path]
    forbidden_root: Path
    artifacts_root: str | None = None
    tools: tuple[BaseTool, ...] = ()
    tool_origins: tuple[ToolOrigin, ...] = ()
    mcp_configs: tuple[dict[str, Any], ...] = ()
    # Declarative child specs; a child ``model`` must be a ``LeaseModelSpec``.
    subagents: tuple[Mapping[str, Any], ...] = ()
    # Stored policy: child ID -> allowed tool names (models are lease-owned).
    child_tools: Mapping[str, frozenset[str]] = field(default_factory=dict)
    custom_middleware: tuple[AgentMiddleware[Any, Any, Any], ...] = ()
    preloaded_resources: Mapping[str, Iterable[str]] = field(default_factory=dict)
    interrupt_on: dict[str, Any] | None = None
    # Interactive (non-trigger) runs: merge each discovered MCP tool's required
    # approval policy into ``interrupt_on`` before compile and digest.
    mcp_interrupts: bool = False
    skills: list[str] | None = None
    memory: list[str] | None = None
    checkpoint_id: str | None = None
    delta_channels: tuple[str, ...] = ()
    checkpoint_child_nodes: tuple[str, ...] = ("tools",)
    versions: Mapping[str, str] = field(default_factory=dict)
    secret_values: frozenset[str] = field(default_factory=frozenset, repr=False)
    secret_reference_ids: tuple[str, ...] = ()
    # Transient preparation resources closed before the marker is written.
    transient_closers: tuple[Closer, ...] = ()


@dataclass(frozen=True, slots=True)
class MaterializerSeams:
    model_factory: ModelFactory = default_model_factory
    mcp_discover: DiscoverTools = default_discover
    # Discovery holds the materialization locks: bounded by small settings values.
    mcp_per_server_timeout: float = field(
        default_factory=lambda: settings.prepared_mcp_discovery_server_timeout_seconds
    )
    mcp_total_timeout: float = field(
        default_factory=lambda: settings.prepared_mcp_discovery_total_timeout_seconds
    )
    mcp_call_timeout: float = 10.0  # post-marker per-call bound (no locks held)
    lease_checkpointer: Callable[..., Awaitable[tuple[Any, CheckpointSeed]]] = (
        default_lease_checkpointer
    )
    compile_child: Callable[..., Any] = create_sub_agent


@dataclass(frozen=True, slots=True)
class MaterializedRun:
    """A staged (``pending_commit``) lease."""

    lease: PreparedRunLease
    mcp_discovery_count: int
