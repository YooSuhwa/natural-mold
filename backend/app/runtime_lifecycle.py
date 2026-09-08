"""Application runtime lifecycle ownership and ordered resource teardown."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from anyio.lowlevel import checkpoint

logger = logging.getLogger(__name__)

ShutdownStep = tuple[str, Callable[[], Awaitable[None]]]
CleanupRunner = Callable[[], Awaitable[list[BaseException]]]


async def run_shutdown_steps(steps: tuple[ShutdownStep, ...]) -> list[BaseException]:
    """Run every owned teardown step and retain failures for the lifespan boundary."""
    errors: list[BaseException] = []
    for name, step in steps:
        try:
            await step()
        except BaseException as error:  # noqa: BLE001 - teardown must continue through every owner
            logger.exception("Shutdown step failed: %s", name)
            errors.append(error)
    return errors


@asynccontextmanager
async def lifespan_cleanup_boundary(cleanup: CleanupRunner) -> AsyncGenerator[None, None]:
    """Always clean up runtime owners while preserving a startup/body failure."""
    primary_error: BaseException | None = None
    try:
        yield
    except BaseException as error:  # noqa: BLE001 - preserve body failure through teardown
        primary_error = error
        raise
    finally:
        cleanup_errors = await cleanup()
        if cleanup_errors and primary_error is None:
            raise cleanup_errors[0]


async def shutdown_runtime_resources() -> list[BaseException]:
    """Stop runtime owners in dependency order, even when an earlier step fails."""
    from app.agent_runtime.checkpointer import shutdown_checkpointer
    from app.agent_runtime.event_broker import registry as broker_registry
    from app.agent_runtime.tool_factory import close_tool_http_client
    from app.database import shutdown_database
    from app.scheduler import release_scheduler_leader, stop_scheduler
    from app.services.conversation_run_worker import get_run_task_registry
    from app.services.skill_evaluation_worker import skill_evaluation_worker
    from app.services.spend_writer import spend_queue

    async def close_skill_worker() -> None:
        await skill_evaluation_worker.stop(timeout_seconds=10.0)

    async def close_run_registry() -> None:
        await get_run_task_registry().shutdown(timeout_seconds=10.0)

    async def close_brokers() -> None:
        closed = broker_registry.close_all()
        if closed:
            logger.info("Shutdown: closed %d live EventBroker(s)", closed)
        await checkpoint()

    async def close_scheduler() -> None:
        stop_scheduler()
        await release_scheduler_leader()

    return await run_shutdown_steps(
        (
            ("skill_worker", close_skill_worker),
            ("run_registry", close_run_registry),
            ("brokers", close_brokers),
            ("scheduler", close_scheduler),
            ("spend_queue", spend_queue.stop),
            ("tool_http_client", close_tool_http_client),
            ("checkpointer", shutdown_checkpointer),
            ("database", shutdown_database),
        )
    )
