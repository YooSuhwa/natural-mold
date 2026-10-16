"""Agent API runs through the prepared lease (parent Task 10, internal I17).

Opt-in behind ``settings.prepared_run_lease_enabled`` (``run_execution`` reads
the flag). Instead of claiming ``queued -> running`` and preparing the agent
inside the provider call, an accepted API run is:

1. materialized through ``materialize_prepared_execution``: under the canonical
   gateway locks ``ApiPreparedRunResolver`` re-authorizes the exact envelope /
   key / deployment / scope / conversation and builds the complete lease; the
   marker pair and ``queued -> running`` commit together, then the lease
   activates (no provider/tool call before activation);
2. executed by acquiring only that exact active lease (``PreparedRunHandle``):
   the runners never call ``_prepare_agent`` and nothing (deployment, thread,
   config, memory, credentials) is read after the marker;
3. terminalized under the same ownership contract as the legacy path (cancel
   polling, ``completed``/``failed``/``canceled``, envelope projection). A
   marker without a live lease (registry loss, second dispatch) durably fails
   the run with ``prepared_run_lease_missing``; it is never rebuilt.

Fallback policy: an agent shape the lease cannot carry fails
``prepared_run_unsupported:<reason>`` before the marker (row still ``queued``);
the run then executes on the unchanged legacy path, with an info log. Any other
preparation failure durably fails the still-queued row and re-raises (the
router maps it to its opaque ``AGENT_API_*`` error). A second local owner of
the same run (``locally_owned``) terminalizes the lease and durably fails the
run with ``prepared_run_locally_owned``.

Behaviour differences from the legacy API path:

* wait mode prepares the whole lease in trigger mode, so linked children also
  run with HiTL/``ask_user`` off (legacy compiled children per invoke runner);
* the request's ``external_user`` is not re-read under the gateway (it never
  affected ``AgentConfig``); the router still records it in audit metadata.

Gates before enabling the flag in production (accepted, documented):

* usage/spend hooks are not run for prepared leases (I20 contract: outside the
  I14 write capability, which exposes no usage writer yet);
* when the gateway can no longer lock the run (owner membership removed, org
  archived) the unmarked row stays ``queued`` exactly like the legacy claim
  failure; archive terminalization / the stale sweep must reclaim it.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import anyio

from app.agent_api.prepared_config import (
    ApiConfigResolver,
    ApiPreparedRunResolver,
    ApiRunTarget,
    ApiScope,
)
from app.agent_runtime import agent_stream_runner
from app.agent_runtime.prepared_child_launch import get_child_launch_ledger
from app.agent_runtime.prepared_run_dispatch import PreparedRunHandle, handle_for
from app.agent_runtime.prepared_run_input_policy import (
    PreparedRunUnsupportedError,
    is_legacy_fallback,
)
from app.agent_runtime.prepared_run_inputs import MaterializerSeams
from app.agent_runtime.prepared_run_registry import PreparedRunLeaseError, get_prepared_run_registry
from app.agent_runtime.prepared_run_transaction import _fail_durably
from app.agent_runtime.runtime_config import AgentConfig, runtime_data_dir
from app.agent_runtime.streaming import StreamErrorRecord
from app.authz.conversation_run_scope import lock_executable_run
from app.services import agent_execution_runtime as owned
from app.services.conversation_run_worker import get_run_task_registry
from app.services.prepared_run_adapter import MemoryReads, PreparedRunRequest, PreparedRunResolver
from app.services.prepared_run_state import fail_prepared_run

if TYPE_CHECKING:
    from app.agent_api.run_execution import ApiOwnedExecution

logger = logging.getLogger(__name__)
PREPARE_FAILED = "prepared_run_prepare_failed"
LOCALLY_OWNED = "prepared_run_locally_owned"


@dataclass(frozen=True, slots=True)
class PreparedApiSeams:
    """Injectable materializer collaborators (tests: scripted models, fake MCP)."""

    materializer: MaterializerSeams | None = None
    memory: MemoryReads | None = None
    run_root_base: Path | None = None


SEAMS = PreparedApiSeams()


@dataclass(frozen=True, slots=True)
class PreparedApiRun:
    handle: PreparedRunHandle
    cfg: AgentConfig  # secret-stripped Langfuse/cost metadata only


def _ownership(request: ApiOwnedExecution) -> owned.RuntimeOwnershipRequest:
    return owned.RuntimeOwnershipRequest(
        request.conversation_run_id,
        request.conversation_id,
        request.root_agent_id,
        request.context,
    )


async def _activated(_lease: object) -> None:
    """The lease is dispatched inline by the request that materialized it."""


async def _fail_unmaterialized(request: owned.RuntimeOwnershipRequest, code: str) -> None:
    """Durably fail our still-queued, unmarked row (a pre-marker failure)."""

    async with owned.async_session() as db:
        run = await lock_executable_run(db, owned._execution_target(request))  # noqa: SLF001
        if run is None or run.status != "queued" or run.runtime_materialized_at is not None:
            await db.rollback()
            return
        await fail_prepared_run(db, run, code)
        await db.commit()


async def materialize_api_run(request: ApiOwnedExecution, scope: ApiScope) -> PreparedApiRun | None:
    """Materialize + activate the lease; ``None`` = unsupported shape, use legacy."""

    seams = SEAMS
    config = ApiConfigResolver(ApiRunTarget(request.public_run_id, request.context, scope))
    inner = PreparedRunResolver(
        PreparedRunRequest(
            context=request.context,
            resolve_config=config,
            run_root_base=seams.run_root_base or runtime_data_dir() / "prepared-runs",
            messages_history=tuple(request.messages),
            # Legacy parity: wait = invoke runner (trigger mode), stream = interactive.
            is_trigger_mode=scope == "invoke",
        ),
        memory=seams.memory,
    )
    prepare = ApiPreparedRunResolver(inner, config)
    ownership = _ownership(request)
    try:
        lease = await owned.materialize_prepared_execution(
            ownership, prepare_inputs=prepare, schedule=_activated, seams=seams.materializer
        )
    except BaseException as exc:
        if isinstance(exc, PreparedRunUnsupportedError) and is_legacy_fallback(exc.reason):
            logger.info(
                "prepared API run %s uses the legacy path: %s",
                request.conversation_run_id,
                exc.code,
            )
            return None
        code = getattr(exc, "code", None)
        with anyio.CancelScope(shield=True):
            await owned._ack_prestart_cancel(ownership)  # noqa: SLF001
            await _fail_unmaterialized(ownership, code if isinstance(code, str) else PREPARE_FAILED)
        raise
    cfg = prepare.execution_cfg
    if cfg is None:
        raise RuntimeError("prepared API run has no execution metadata")
    return PreparedApiRun(handle_for(lease), cfg)


async def _fail_terminal(runtime: owned._OwnedRuntime, error: BaseException, digest: str) -> None:  # noqa: SLF001
    if isinstance(error, PreparedRunLeaseError):
        await _fail_durably(
            lambda: owned.async_session(),
            owned._execution_target(runtime.request),  # noqa: SLF001
            fail_prepared_run,
            error.code,
            digest,
        )
    await owned._finish(runtime, "failed")  # noqa: SLF001


async def _abandon_lease(runtime: owned._OwnedRuntime, digest: str) -> None:  # noqa: SLF001
    """Close a lease this request cannot own, then durably fail its marked run."""

    run_id = runtime.request.run_id
    get_child_launch_ledger().terminalize(run_id)
    await get_prepared_run_registry().terminalize(run_id)
    await _fail_durably(
        lambda: owned.async_session(),
        owned._execution_target(runtime.request),  # noqa: SLF001
        fail_prepared_run,
        LOCALLY_OWNED,
        digest,
    )


@asynccontextmanager
async def _prepared_ownership(
    request: ApiOwnedExecution, prepared: PreparedApiRun
) -> AsyncIterator[None]:
    """The legacy ownership lifecycle minus the claim (materialization marked running)."""

    registry = get_run_task_registry()
    runtime = owned._OwnedRuntime(_ownership(request), registry.worker_instance_id)  # noqa: SLF001
    run_id = request.conversation_run_id
    existing = registry.get(run_id)
    if existing is not None and not existing.done():
        with anyio.CancelScope(shield=True):
            await _abandon_lease(runtime, prepared.handle.digest)
        raise owned.RuntimeOwnershipConflict(run_id, "locally_owned")
    registry.start_current(run_id)
    try:
        failure: BaseException | None = None
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(owned._poll_cancel, runtime)  # noqa: SLF001
            try:
                yield
            except (GeneratorExit, anyio.get_cancelled_exc_class()) as error:
                with anyio.CancelScope(shield=True):
                    await owned._finish(runtime, "canceled")  # noqa: SLF001
                failure = error
            except BaseException as error:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
                with anyio.CancelScope(shield=True):
                    await _fail_terminal(runtime, error, prepared.handle.digest)
                failure = error
            else:
                if not await owned._finish(runtime, "completed"):  # noqa: SLF001
                    raise owned.RuntimeOwnershipConflict(run_id, "owner_changed")
            finally:
                tasks.cancel_scope.cancel()
        if failure is not None:
            raise failure
    finally:
        registry.discard(run_id)


async def execute_prepared_wait(request: ApiOwnedExecution, prepared: PreparedApiRun) -> str:
    async with _prepared_ownership(request, prepared):
        return await agent_stream_runner.execute_prepared_agent_invoke(
            prepared.cfg, prepared.handle, moldy_source="api", run_id=request.public_run_id
        )


async def stream_prepared_run(
    request: ApiOwnedExecution, prepared: PreparedApiRun
) -> AsyncGenerator[str, None]:
    async with _prepared_ownership(request, prepared):
        errors: list[StreamErrorRecord] = []
        saw_error_event = False
        stream = agent_stream_runner._run_prepared_agent_stream(  # noqa: SLF001
            prepared.cfg,
            prepared.handle,
            error_sink=errors,
            run_id=request.public_run_id,
            moldy_source="api",
        )
        async with aclosing(stream) as chunks:
            async for chunk in chunks:
                if chunk.lstrip().startswith("event: error"):
                    saw_error_event = True
                    continue
                yield chunk
        if errors:
            raise RuntimeError(errors[0].message)
        if saw_error_event:
            raise RuntimeError("Agent run failed")
