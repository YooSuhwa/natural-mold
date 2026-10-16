"""Build one complete prepared-run lease before the marker (parent Task 10, I15).

Runs inside the caller's canonical gateway transaction (locks held) and
returns a *staged* (``pending_commit``) lease. Order:

``snapshot/seed -> MCP discovery -> lease models -> child catalog -> probe
compile -> census -> reserve -> digest + authority -> install interceptors and
leaf guards -> compile children bottom-up (every response variant) -> compile
root -> verify surface -> stage``.

The final check compares the *tool* surface only (root ToolNode and every
compiled ``(child, variant)`` ToolNode against the census); middleware and
backends are not re-compared, they are fixed inputs of both passes. The
prepared root and children use the installed ``FilesystemMiddleware``/core
stack (installed parity): Moldy's compatibility wrapper is deliberately not
applied, since it would add a non-allowlisted filesystem/todo wrapper ahead of
the app slice.

Contract for the caller: after a failure that happened post-``reserve`` (the
run ID is retired) the queued row can never be materialized again, so the
caller (internal I16) must durably fail that queued row.

Reservation follows the census (not the final compile) because the I12 leaf
guards, child-launch admission and model send guards bind the reserved token
and digest at construction; the probe compile is the installed compiler run on
the identical inputs, and the final compile must reproduce its tool surface.
No provider, tool or child is dispatched here: the graphs are only compiled.
Any failure closes every lease-owned resource exactly once and retires the
reservation, so the run can never be re-reserved and rebuilt.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from types import MappingProxyType
from typing import Any

from app.agent_runtime.lease_bound_tool import LeaseBoundToolMiddleware
from app.agent_runtime.lease_dispatch_permit import SessionFactory
from app.agent_runtime.lease_snapshot_backend import LeaseSnapshotBackend
from app.agent_runtime.mcp_preparation import prepare_mcp_tools
from app.agent_runtime.prepared_child_launch import PreparedChildLaunchAdmission
from app.agent_runtime.prepared_run_compile import (
    PreparedCompileError,
    compile_children,
    compile_root,
    refused_admission,
    runnable_tool_names,
)
from app.agent_runtime.prepared_run_inputs import (
    MaterializedRun,
    MaterializerSeams,
    PreparationInputs,
)
from app.agent_runtime.prepared_run_participants import (
    build_censuses,
    build_lease_models,
    child_policy,
    lease_digest,
    mcp_interrupt_on,
    model_specs,
    with_models,
)
from app.agent_runtime.prepared_run_registry import get_prepared_run_registry
from app.agent_runtime.prepared_run_support import (
    LateBoundAuthority,
    OnceCloser,
    bounded_cleanup,
    unbound_lease,
)
from app.agent_runtime.prepared_subagent_catalog import ROOT_PATH, build_prepared_child_catalog
from app.agent_runtime.prepared_task_tool_handler import PreparedStateProjector
from app.agent_runtime.runtime_config import PreparedRunBinding
from app.agent_runtime.runtime_effective_capabilities import tool_node_names
from app.agent_runtime.tool_access_policy import ToolOrigin

Trace = Callable[[str], None]


def _noop(_event: str) -> None:
    return None


async def materialize_prepared_lease(
    binding: PreparedRunBinding,
    inputs: PreparationInputs,
    *,
    session_factory: SessionFactory,
    seams: MaterializerSeams | None = None,
    trace: Trace = _noop,
) -> MaterializedRun:
    """Return a staged complete lease or raise after closing everything once."""

    seams = seams or MaterializerSeams()
    registry = get_prepared_run_registry()
    closers = OnceCloser()
    reserved: PreparedRunBinding | None = None
    try:
        saver, seed = await seams.lease_checkpointer(
            thread_id=inputs.thread_id,
            checkpoint_id=inputs.checkpoint_id,
            run_id=str(binding.run_id),
            child_node_names=inputs.checkpoint_child_nodes,
            delta_channels=inputs.delta_channels,
        )
        backend = LeaseSnapshotBackend(
            inputs.snapshot_catalog,
            run_root=inputs.run_root,
            writable_roots=inputs.writable_roots,
            forbidden_root=inputs.forbidden_root,
            artifacts_root=inputs.artifacts_root,
        )
        trace("snapshot_seed")
        mcp = await prepare_mcp_tools(
            list(inputs.mcp_configs),
            per_server_timeout=seams.mcp_per_server_timeout,
            total_timeout=seams.mcp_total_timeout,
            call_timeout=seams.mcp_call_timeout,
            discover=seams.mcp_discover,
        )
        trace("mcp_discovery")
        authority = LateBoundAuthority()
        specs = model_specs(inputs)
        models = build_lease_models(
            specs,
            binding=binding,
            authority=authority,
            session_factory=session_factory,
            factory=seams.model_factory,
            closers=closers,
        )
        trace("lease_models")
        tools = [*inputs.tools, *(item.tool for item in mcp.tools)]
        origins = [
            *inputs.tool_origins,
            *(ToolOrigin(item.tool_name, "mcp", item.descriptor_id) for item in mcp.tools),
        ]
        root_model = models[ROOT_PATH].model
        catalog = build_prepared_child_catalog(
            with_models(inputs.subagents, ROOT_PATH, models),
            model=root_model,
            tools=tools,
            permissions=None,
            stored_policy=child_policy(inputs, models),
        )
        common: dict[str, Any] = {
            "backend": backend,
            "projector": PreparedStateProjector(),
            "interrupt_on": mcp_interrupt_on(inputs, mcp),
            "skills": inputs.skills,
        }
        root_common = {
            **common,
            "model": root_model,
            "tools": tools,
            "system_prompt": inputs.system_prompt,
            "catalog": catalog,
            "memory": inputs.memory,
        }
        child_common = {**common, "parent_middleware": inputs.custom_middleware}
        probe_runnables, child_surfaces = compile_children(
            catalog,
            admission_factory=refused_admission,
            guards=None,
            compile_child=None,
            **child_common,
        )
        probe = compile_root(
            runnables=probe_runnables,
            admission_factory=refused_admission,
            app_middleware=inputs.custom_middleware,
            checkpointer=None,
            **root_common,
        )
        surfaces = {ROOT_PATH: tool_node_names(probe), **child_surfaces}
        trace("probe_compile")
        censuses = build_censuses(
            surfaces, inputs=inputs, catalog=catalog, origins=origins, backend=backend, specs=specs
        )
        trace("census")
        run_binding = replace(binding, participant_paths=(ROOT_PATH, *catalog.children))
        token = registry.reserve(run_binding)
        reserved = run_binding
        trace("reserve")
        digest, descriptor_ids, tool_access = lease_digest(
            run_binding,
            inputs=inputs,
            catalog=catalog,
            mcp=mcp,
            censuses=censuses,
            seed_checkpoint_id=seed.checkpoint_id,
        )
        lease = replace(
            unbound_lease(run_binding),
            token=token,
            digest=digest,
            tool_access=MappingProxyType(tool_access),
            descriptor_ids=descriptor_ids,
        )
        authority.lease = lease
        admission = PreparedChildLaunchAdmission(
            lease,
            catalog=catalog,
            catalog_digest=catalog.digest,
            allowed_descriptors=descriptor_ids,
            session_factory=session_factory,
        )
        guards = {
            path: LeaseBoundToolMiddleware(lease, census, session_factory=session_factory)
            for path, census in censuses.items()
        }
        trace("install_interceptors")
        runnables, _final_specs = compile_children(
            catalog,
            admission_factory=lambda: admission,
            guards=guards,
            compile_child=seams.compile_child,
            **child_common,
        )
        trace("compile_children")
        root = compile_root(
            runnables=runnables,
            admission_factory=lambda: admission,
            app_middleware=(guards[ROOT_PATH], *inputs.custom_middleware),
            checkpointer=saver,
            **root_common,
        )
        trace("compile_root")
        if tool_node_names(root) != surfaces[ROOT_PATH] or any(
            runnable_tool_names(runnable) != surfaces[child_id]
            for (child_id, _variant), runnable in runnables.items()
        ):
            raise PreparedCompileError("effective_surface_changed")
        configurable: dict[str, Any] = {
            "thread_id": inputs.thread_id,
            "moldy_run_id": str(binding.run_id),
        }
        if inputs.checkpoint_id:
            configurable["checkpoint_id"] = inputs.checkpoint_id
        lease = replace(
            lease,
            runnable=root,
            child_runnables=runnables,
            messages=inputs.messages,
            config=MappingProxyType({"configurable": configurable}),
            checkpointer=saver,
            checkpoint_seed=seed,
            snapshots=MappingProxyType({"catalog": inputs.snapshot_catalog, "mcp": mcp}),
            secret_values=inputs.secret_values,
            close=closers,
        )
        authority.lease = lease
        await registry.stage(lease)
        trace("stage")
        return MaterializedRun(lease, mcp.discovery_count)
    except BaseException:
        try:
            await bounded_cleanup(closers, label="lease_resources")
        finally:
            if reserved is not None:
                run_id = reserved.run_id
                await bounded_cleanup(lambda: registry.terminalize(run_id), label="reservation")
        raise
