"""Per-participant pieces of prepared-run materialization (parent Task 10, I15).

Participants are ``root`` and every prepared child path. Each participant with
its own ``LeaseModelSpec`` receives one lease-owned model whose send guard is
late-bound to the authority lease; children without one inherit the nearest
ancestor's model. The census closes each participant's effective surface and
the digest covers identity, versions, snapshot/catalog/MCP/census digests,
participant-qualified tool classes, checkpoint lineage, secret references and
every descriptor ID — never secret values or snapshot content.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.agent_runtime.lease_bound_model import LeaseSendGuard
from app.agent_runtime.lease_dispatch_permit import SessionFactory
from app.agent_runtime.lease_model_factory import LeaseModelSpec
from app.agent_runtime.prepared_child_launch import child_launch_descriptors
from app.agent_runtime.prepared_mcp_resources import PreparedMcpResources
from app.agent_runtime.prepared_run_compile import PreparedCompileError
from app.agent_runtime.prepared_run_digest import (
    PreparedRunDigestMaterial,
    compute_prepared_run_digest,
)
from app.agent_runtime.prepared_run_inputs import ModelFactory, OwnedModel, PreparationInputs
from app.agent_runtime.prepared_run_support import (
    LateBoundAuthority,
    OnceCloser,
    participant_census,
    provider_descriptor_id,
    unbound_lease,
)
from app.agent_runtime.prepared_subagent_catalog import (
    ROOT_PATH,
    PreparedChildCatalog,
    PreparedChildPolicy,
    model_identity,
)
from app.agent_runtime.runtime_config import PreparedRunBinding
from app.agent_runtime.runtime_effective_capabilities import (
    EffectiveCapabilityCensus,
    ProviderOrigin,
    lease_descriptor_ids,
)
from app.agent_runtime.tool_access_policy import ToolOrigin


def model_specs(inputs: PreparationInputs) -> dict[str, LeaseModelSpec]:
    specs = {ROOT_PATH: inputs.model}

    def walk(children: Sequence[Mapping[str, Any]], parent: str) -> None:
        for child in children:
            path = f"{parent}/{child.get('name')}"
            model = child.get("model")
            if model is not None:
                if not isinstance(model, LeaseModelSpec):
                    raise PreparedCompileError("child_model_not_lease_spec", path)
                specs[path] = model
            walk(child.get("subagents", ()), path)

    walk(inputs.subagents, ROOT_PATH)
    return specs


def providers(path: str, specs: Mapping[str, LeaseModelSpec]) -> tuple[ProviderOrigin, ...]:
    spec = specs.get(path)
    return () if spec is None else (ProviderOrigin("primary", spec.provider, spec.model_name),)


def build_lease_models(
    specs: Mapping[str, LeaseModelSpec],
    *,
    binding: PreparedRunBinding,
    authority: LateBoundAuthority,
    session_factory: SessionFactory,
    factory: ModelFactory,
    closers: OnceCloser,
) -> dict[str, OwnedModel]:
    models: dict[str, OwnedModel] = {}
    for path, spec in specs.items():
        guard = LeaseSendGuard(
            lease=unbound_lease(binding),
            descriptor_id=provider_descriptor_id(path, providers(path, specs)[0]),
            allowed_descriptors=frozenset(),
            session_factory=session_factory,
            permit_opener=authority.open,
        )
        models[path] = factory(spec, guard)
        closers.add(models[path].aclose)
    return models


def _owner(path: str, models: Mapping[str, OwnedModel]) -> str:
    while path not in models:
        path = path.rpartition("/")[0]
    return path


def child_policy(
    inputs: PreparationInputs, models: Mapping[str, OwnedModel]
) -> dict[str, PreparedChildPolicy]:
    return {
        path: PreparedChildPolicy(model_identity(models[_owner(path, models)].model), allowed)
        for path, allowed in inputs.child_tools.items()
    }


def with_models(
    children: Sequence[Mapping[str, Any]], parent: str, models: Mapping[str, OwnedModel]
) -> list[dict[str, Any]]:
    resolved = []
    for child in children:
        path = f"{parent}/{child.get('name')}"
        spec = dict(child)
        if path in models:
            spec["model"] = models[path].model
        if "subagents" in spec:
            spec["subagents"] = with_models(spec["subagents"], path, models)
        resolved.append(spec)
    return resolved


def build_censuses(
    surfaces: Mapping[str, Sequence[str]],
    *,
    inputs: PreparationInputs,
    catalog: PreparedChildCatalog,
    origins: Sequence[ToolOrigin],
    backend: Any,
    specs: Mapping[str, LeaseModelSpec],
) -> dict[str, EffectiveCapabilityCensus]:
    def middleware(path: str) -> tuple[Any, ...]:
        if path == ROOT_PATH:
            return inputs.custom_middleware
        return tuple(catalog.children[path].spec.get("middleware", ()))

    return {
        path: participant_census(
            path,
            surface=surface,
            origins=origins,
            middleware=middleware(path),
            backend=backend,
            providers=providers(path, specs),
            preloaded=inputs.preloaded_resources,
        )
        for path, surface in surfaces.items()
    }


def lease_digest(
    binding: PreparedRunBinding,
    *,
    inputs: PreparationInputs,
    catalog: PreparedChildCatalog,
    mcp: PreparedMcpResources,
    censuses: Mapping[str, EffectiveCapabilityCensus],
    seed_checkpoint_id: str | None,
) -> tuple[str, frozenset[str], dict[str, str]]:
    """Return ``(digest, descriptor_ids, tool_access)`` for the lease."""

    descriptor_ids = lease_descriptor_ids(censuses.values()) | child_launch_descriptors(catalog)
    tool_access = {k: v for c in censuses.values() for k, v in c.tool_access().items()}
    material = PreparedRunDigestMaterial(
        binding=binding,
        versions=inputs.versions,
        snapshot_digests={
            **inputs.snapshot_catalog.digests(),
            "child_catalog": catalog.digest,
            "mcp": mcp.digest,
            **{f"census:{path}": census.digest() for path, census in censuses.items()},
        },
        tool_access=tool_access,
        checkpoint_lineage=(inputs.thread_id, "", seed_checkpoint_id),
        secret_reference_ids=(
            *inputs.secret_reference_ids,
            *(ref for item in mcp.tools for ref in item.secret_reference_ids),
        ),
        descriptor_ids=descriptor_ids,
    )
    return compute_prepared_run_digest(material), descriptor_ids, tool_access
