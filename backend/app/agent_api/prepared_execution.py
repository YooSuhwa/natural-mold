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

Run roots default to ``prepared_conversation_executor.prepared_run_root()``
(outside the runtime data root the lease backend forbids) and are removed
after terminalization; like the trigger entry, output files are not persisted.

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
from app.agent_runtime.prepared_run_dispatch import PreparedRunHandle, handle_for
from app.agent_runtime.prepared_run_input_policy import (
    PreparedRunUnsupportedError,
    is_legacy_fallback,
)
from app.agent_runtime.prepared_run_inputs import MaterializerSeams
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.streaming import StreamErrorRecord
from app.authz.conversation_run_scope import lock_executable_run
from app.database import tenant_session
from app.exceptions import AppError
from app.services import agent_execution_runtime as owned
from app.services import prepared_conversation_executor
from app.services.agent_execution_lifecycle import (
    OwnedRuntime,
    execution_target,
    supervise_owned_run,
)
from app.services.agent_execution_supervision import ack_prestart_cancel
from app.services.conversation_run_worker import get_run_task_registry
from app.services.prepared_lease_cleanup import (
    abandon_activated_lease,
    fail_owned_run,
    remove_run_root,
)
from app.services.prepared_run_adapter import MemoryReads, PreparedRunRequest, PreparedRunResolver
from app.services.prepared_run_state import fail_prepared_run

if TYPE_CHECKING:
    from app.agent_api.run_execution import ApiOwnedExecution

logger = logging.getLogger(__name__)
PREPARE_FAILED = "prepared_run_prepare_failed"
LOCALLY_OWNED = "prepared_run_locally_owned"
METADATA_MISSING = "prepared_run_metadata_missing"


class PreparedApiRunError(RuntimeError):
    """A coded pre-marker failure whose text is the machine code only (no paths)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


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

    async with tenant_session(request.context, maker=owned.async_session) as db:
        run = await lock_executable_run(db, execution_target(request))
        if run is None or run.status != "queued" or run.runtime_materialized_at is not None:
            await db.rollback()
            return
        await fail_prepared_run(db, run, code)
        await db.commit()


def _run_root_base() -> Path:
    """Run roots live outside the runtime data root (the lease backend forbids sharing)."""

    return SEAMS.run_root_base or prepared_conversation_executor.prepared_run_root()


def _public(exc: BaseException) -> BaseException:
    """Coded errors whose text carries more than the code (e.g. a physical run-root
    path) are re-raised as the code alone; envelope/audit only ever see ``str(exc)``."""

    code = getattr(exc, "code", None)
    if isinstance(exc, AppError) or not isinstance(code, str) or str(exc) == code:
        return exc
    return PreparedApiRunError(code)


async def materialize_api_run(request: ApiOwnedExecution, scope: ApiScope) -> PreparedApiRun | None:
    """Materialize + activate the lease; ``None`` = unsupported shape, use legacy."""

    seams = SEAMS
    config = ApiConfigResolver(ApiRunTarget(request.public_run_id, request.context, scope))
    inner = PreparedRunResolver(
        PreparedRunRequest(
            context=request.context,
            resolve_config=config,
            run_root_base=_run_root_base(),
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
            await ack_prestart_cancel(ownership)
            await _fail_unmaterialized(ownership, code if isinstance(code, str) else PREPARE_FAILED)
            await remove_run_root(_run_root_base() / str(ownership.run_id))
        public = _public(exc)
        if public is exc:
            raise
        raise public from exc
    cfg = prepare.execution_cfg
    if cfg is None:
        # Activated but unusable: never leave the ACTIVE lease behind (I24 H4).
        await abandon_activated_lease(execution_target(ownership), lease.digest)
        await remove_run_root(_run_root_base() / str(ownership.run_id))
        raise PreparedApiRunError(METADATA_MISSING)
    return PreparedApiRun(handle_for(lease), cfg)


@asynccontextmanager
async def _prepared_ownership(
    request: ApiOwnedExecution, prepared: PreparedApiRun
) -> AsyncIterator[None]:
    """The legacy ownership lifecycle minus the claim (materialization marked running)."""

    runtime = OwnedRuntime(_ownership(request), get_run_task_registry().worker_instance_id)
    digest = prepared.handle.digest

    owns_root = True

    async def locally_owned() -> None:
        nonlocal owns_root
        owns_root = False  # the live local owner still uses this run root
        await abandon_activated_lease(execution_target(runtime.request), digest, LOCALLY_OWNED)

    async def failed(owner: OwnedRuntime, error: BaseException) -> None:
        await fail_owned_run(owner, error, digest)

    try:
        # Cancel poll + heartbeat so another worker's stale sweep spares us (M4).
        async with supervise_owned_run(
            runtime, heartbeat=True, on_failure=failed, on_locally_owned=locally_owned
        ):
            yield
    finally:
        if owns_root:
            await remove_run_root(_run_root_base() / str(request.conversation_run_id))


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
            # Chained so the shared error-code table sees a mid-run denial (M2).
            raise RuntimeError(errors[0].message) from errors[0].error
        if saw_error_event:
            raise RuntimeError("Agent run failed")
