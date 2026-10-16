"""Prepared-lease start for owned conversation runs (parent Task 10, I16/I19).

Every chat entry (send/start, edit, regenerate, resume, v3 protocol run.start /
input.respond / fork) and every queue launch reaches ``_run_conversation``.
With ``settings.prepared_run_lease_enabled`` on and one of the four known
stream executors, that worker's :func:`start_owned_run` replaces the legacy
``queued -> running`` transition:

1. capture the run-metrics checkpoint baseline (pre-marker read);
2. ``materialize_prepared_execution``: canonical gateway locks, then the
   ``PreparedRunResolver`` re-resolves the chat config *under those locks*
   (``chat_config_resolver`` with the entry's exact ``checkpoint_id`` so the
   edit/fork seed is preserved), builds/compiles the complete lease, writes
   the marker pair + ``queued -> running``, commits and activates;
3. return an executor that streams the exact lease (``prepared_dispatch``)
   with the pre-marker stream input: frozen converted messages (chat),
   the caller's dict (fork-edit ``Overwrite`` / v3 / queue payload), empty
   messages (regenerate time travel) or ``Command(resume=...)`` (HiTL).

``prepared_run_unsupported:*`` raised by the resolver (before any
reservation) deterministically falls back to the legacy path, logged and
counted in :data:`FALLBACKS`: OpenAI moderation, fallback chains, configured
middleware, memory tools and legacy-compat policies are not leasable yet.
``RUN_NOT_MATERIALIZABLE`` (not queued / authority gone) is classified by the
legacy transition without starting. Every other failure is raised as
:class:`PreparedStartError` so the worker durably fails the still-queued row
(the I15 caller contract). The regenerate entry's ``config_transform`` is
reapplied to the locked re-resolution.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.agent_runtime import prepared_run_inputs
from app.agent_runtime.prepared_child_launch import get_child_launch_ledger
from app.agent_runtime.prepared_run_dispatch import PreparedRunHandle, handle_for
from app.agent_runtime.prepared_run_input_policy import (
    PreparedRunUnsupportedError,
    is_legacy_fallback,
)
from app.agent_runtime.prepared_run_registry import get_prepared_run_registry
from app.agent_runtime.prepared_run_transaction import (
    RUN_NOT_MATERIALIZABLE,
    PreparedRunTransactionError,
)
from app.agent_runtime.run_error_codes import PREPARED_START_FAILED
from app.agent_runtime.run_metrics import RunMetricsAccumulator
from app.agent_runtime.runtime_config import AgentConfig, PreparedRunBinding, PreparedRunLease
from app.authz.conversation_run_scope import RunExecutionTarget
from app.dependencies import CurrentUser
from app.models.conversation_run import ConversationRun
from app.services.conversation_run_task_registry import worker_attr
from app.services.prepared_conversation_executor import (
    Executor,
    PreparedRunner,
    prepared_executor,
    prepared_run_root,
    prepared_runner_for,
    stream_shape,
)
from app.services.prepared_run_adapter import (
    PreparedRunRequest,
    PreparedRunResolver,
    ResolveConfig,
    chat_config_resolver,
)

logger = logging.getLogger("app.services.conversation_run_worker")

ConfigTransform = Callable[[AgentConfig], AgentConfig]
Transition = Callable[..., Awaitable[tuple[ConversationRun | None, bool]]]
# Deterministic legacy fallbacks, keyed by the unsupported reason.
FALLBACKS: Counter[str] = Counter()


class PreparedStartError(RuntimeError):
    """A prepared start failed before the run started; the row is still queued."""

    code = PREPARED_START_FAILED

    def __init__(self) -> None:
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class OwnedRunInputs:
    cfg: AgentConfig
    user: CurrentUser
    input_payload: Any
    executor_fn: Any
    config_transform: ConfigTransform | None = None


@dataclass(frozen=True, slots=True)
class PreparedRunStart:
    run: ConversationRun | None
    started: bool
    executor: Executor | None = None
    run_metrics: RunMetricsAccumulator | None = None
    # Resolver's locked metadata config + the lease's run-scoped secret set: the
    # worker uses it for redaction, cost model and the artifact recorder.
    cfg: AgentConfig | None = None
    binding: PreparedRunBinding | None = None


async def release_unclaimed(start: PreparedRunStart | None) -> None:
    """Close an activated lease the stream never claimed (idempotent).

    Between activation and the executor's dispatch claim the worker still
    awaits audit/heartbeat/metrics work; a failure or cancellation there must
    not leave a live lease behind. A dispatched lease is already terminalized
    by ``prepared_dispatch``; this is then a no-op.
    """

    if start is None or start.binding is None:
        return
    run_id = start.binding.run_id
    get_child_launch_ledger().terminalize(run_id)
    await get_prepared_run_registry().terminalize(run_id)


def lease_output_dir(start: PreparedRunStart | None) -> Path | None:
    """The lease-routed ``/conversations/<thread_id>`` output dir (artifact index, M1)."""

    if start is None or start.binding is None:
        return None
    return prepared_run_root() / str(start.binding.run_id) / "output"


async def remove_lease_run_root(start: PreparedRunStart | None) -> None:
    """Remove the run's scratch/output root after terminalization (best effort, M1).

    Artifacts were already copied into artifact storage by the recorder.
    """

    if start is None or start.binding is None:
        return
    from app.services.prepared_lease_cleanup import remove_run_root

    await remove_run_root(prepared_run_root() / str(start.binding.run_id))


def _resolve_config(inputs: OwnedRunInputs) -> ResolveConfig:
    cfg, transform = inputs.cfg, inputs.config_transform
    base = chat_config_resolver(inputs.user, cfg.org_context, checkpoint_id=cfg.checkpoint_id)
    if transform is None:
        return base

    async def resolve(db: Any, run: ConversationRun) -> AgentConfig:
        return transform(await base(db, run))

    return resolve


async def start_owned_run(
    target: RunExecutionTarget,
    inputs: OwnedRunInputs,
    *,
    worker_instance_id: str,
    started_at: float,
    transition: Transition,
) -> PreparedRunStart:
    """Start through the prepared lease when enabled, else the legacy transition."""

    runner = prepared_runner_for(inputs.executor_fn)
    if runner is not None:
        try:
            start = await _start_prepared(
                target, inputs, runner, worker_instance_id, started_at, transition
            )
        except Exception as error:
            raise PreparedStartError from error
        if start is not None:
            return start
    run, started = await transition(target, worker_instance_id=worker_instance_id)
    return PreparedRunStart(run, started)


async def _start_prepared(
    target: RunExecutionTarget,
    inputs: OwnedRunInputs,
    runner: PreparedRunner,
    worker_instance_id: str,
    started_at: float,
    transition: Transition,
) -> PreparedRunStart | None:
    from app.services.agent_execution_runtime import (
        RuntimeOwnershipRequest,
        materialize_prepared_execution,
    )

    conversation = target.conversation
    history, stream_input = stream_shape(runner, inputs.input_payload)
    metrics = await worker_attr("_prepare_run_metrics")(
        conversation_id=conversation.conversation_id, cfg=inputs.cfg, started_at=started_at
    )
    resolver = PreparedRunResolver(
        PreparedRunRequest(
            context=conversation.context,
            resolve_config=_resolve_config(inputs),
            run_root_base=prepared_run_root(),
            messages_history=history,
        )
    )
    locked: list[ConversationRun] = []
    handles: list[tuple[PreparedRunHandle, frozenset[str]]] = []

    async def prepare(db: Any, run: ConversationRun) -> Any:
        prepared = await resolver(db, run)
        locked.append(run)
        return prepared

    async def schedule(lease: PreparedRunLease) -> None:
        handles.append((handle_for(lease), lease.secret_values))

    request = RuntimeOwnershipRequest(
        target.run_id, conversation.conversation_id, conversation.agent_id, conversation.context
    )
    try:
        await materialize_prepared_execution(
            request,
            prepare_inputs=prepare,
            schedule=schedule,
            seams=prepared_run_inputs.materializer_seams(),
        )
    except PreparedRunUnsupportedError as error:
        if locked or not is_legacy_fallback(error.reason):
            raise
        FALLBACKS[error.reason] += 1
        logger.info("prepared run fallback run_id=%s reason=%s", target.run_id, error.code)
        return None
    except PreparedRunTransactionError as error:
        if error.code != RUN_NOT_MATERIALIZABLE:
            raise
        run, _ = await transition(target, worker_instance_id=worker_instance_id, start=False)
        return PreparedRunStart(run, started=False)
    ((handle, secrets),) = handles
    metadata = resolver.execution_cfg or inputs.cfg
    worker_cfg = replace(metadata, secret_values=set(secrets) | inputs.cfg.secret_values)
    executor = prepared_executor(runner, handle, stream_input, metadata, worker_instance_id)
    return PreparedRunStart(
        locked[0],
        started=True,
        executor=executor,
        run_metrics=metrics,
        cfg=worker_cfg,
        binding=handle.binding,
    )
