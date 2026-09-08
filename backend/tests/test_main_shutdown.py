"""Ordered application teardown contracts."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI

import app.main as main
import app.runtime_lifecycle as runtime_lifecycle
from app.runtime_lifecycle import lifespan_cleanup_boundary, run_shutdown_steps


@pytest.mark.asyncio
async def test_shutdown_steps_continue_after_failure_in_order() -> None:
    # Given three resource owners where the first close fails.
    calls: list[str] = []

    class FirstCloseFailure(RuntimeError):
        pass

    async def first() -> None:
        calls.append("first")
        raise FirstCloseFailure

    async def second() -> None:
        calls.append("second")

    async def third() -> None:
        calls.append("third")

    # When ordered teardown runs.
    errors = await run_shutdown_steps((("first", first), ("second", second), ("third", third)))

    # Then every owner is attempted and the primary cleanup error is retained.
    assert calls == ["first", "second", "third"]
    assert len(errors) == 1
    assert isinstance(errors[0], FirstCloseFailure)


@pytest.mark.asyncio
async def test_lifespan_boundary_preserves_body_error_after_cleanup_failure() -> None:
    # Given a body failure and a later cleanup failure.
    class BodyFailure(RuntimeError):
        pass

    class CleanupFailure(RuntimeError):
        pass

    cleanup_called = False

    async def cleanup() -> list[BaseException]:
        nonlocal cleanup_called
        cleanup_called = True
        return [CleanupFailure()]

    # When the body exits through the cleanup boundary.
    with pytest.raises(BodyFailure):
        async with lifespan_cleanup_boundary(cleanup):
            raise BodyFailure

    # Then cleanup ran but did not replace the primary body error.
    assert cleanup_called is True


@pytest.mark.asyncio
async def test_lifespan_boundary_raises_cleanup_error_without_body_failure() -> None:
    # Given a successful body and one cleanup failure.
    class CleanupFailure(RuntimeError):
        pass

    failure = CleanupFailure()

    async def cleanup() -> list[BaseException]:
        return [failure]

    # When the body exits normally, then the first cleanup failure is raised.
    with pytest.raises(CleanupFailure) as caught:
        async with lifespan_cleanup_boundary(cleanup):
            pass
    assert caught.value is failure


@pytest.mark.asyncio
async def test_lifespan_cleans_up_when_inner_startup_enter_fails(monkeypatch) -> None:
    # Given startup has acquired resources before its context entry fails, and cleanup also fails.
    calls: list[str] = []

    class StartupFailure(RuntimeError):
        pass

    class CleanupFailure(RuntimeError):
        pass

    startup_failure = StartupFailure()

    @asynccontextmanager
    async def failing_startup(_app: FastAPI) -> AsyncGenerator[None, None]:
        raise startup_failure
        yield

    async def cleanup() -> list[BaseException]:
        calls.append("cleanup")
        return [CleanupFailure()]

    monkeypatch.setattr(main, "_lifespan_started", failing_startup)
    monkeypatch.setattr(main, "shutdown_runtime_resources", cleanup)

    # When FastAPI enters the public lifespan.
    with pytest.raises(StartupFailure) as caught:
        async with main.lifespan(FastAPI()):
            pass

    # Then cleanup runs once and cannot replace the startup failure.
    assert caught.value is startup_failure
    assert calls == ["cleanup"]


@pytest.mark.asyncio
async def test_runtime_shutdown_disposes_database_after_checkpointer_close_failure(
    monkeypatch,
) -> None:
    # Given ordered shutdown collaborators and a checkpointer close failure.
    import app.agent_runtime.checkpointer as checkpointer
    import app.agent_runtime.event_broker as event_broker
    import app.agent_runtime.tool_factory as tool_factory
    import app.database as database
    import app.scheduler as scheduler_module
    import app.services.conversation_run_worker as conversation_run_worker

    calls: list[str] = []

    class CheckpointerCloseFailure(RuntimeError):
        pass

    failure = CheckpointerCloseFailure()

    async def stop_skill_worker(*_args: object, **_kwargs: object) -> None:
        calls.append("skill_worker")

    async def stop_run_registry(*_args: object, **_kwargs: object) -> None:
        calls.append("run_registry")

    def stop_scheduler() -> None:
        calls.append("scheduler_stop")

    async def release_leader() -> None:
        calls.append("scheduler_release")

    async def stop_spend_queue() -> None:
        calls.append("spend_queue")

    async def close_tool_client() -> None:
        calls.append("tool_http_client")

    async def close_checkpointer() -> None:
        calls.append("checkpointer")
        raise failure

    async def dispose_database() -> None:
        calls.append("database")

    class RunRegistry:
        async def shutdown(self, **_kwargs: object) -> None:
            await stop_run_registry()

    class SkillWorker:
        async def stop(self, *_args: object, **_kwargs: object) -> None:
            await stop_skill_worker()

    class SpendQueue:
        async def stop(self) -> None:
            await stop_spend_queue()

    import app.services.skill_evaluation_worker as skill_evaluation_worker_module
    import app.services.spend_writer as spend_writer

    monkeypatch.setattr(skill_evaluation_worker_module, "skill_evaluation_worker", SkillWorker())
    monkeypatch.setattr(conversation_run_worker, "get_run_task_registry", lambda: RunRegistry())
    monkeypatch.setattr(event_broker.registry, "close_all", lambda: 0)

    def unexpected_scheduler_creation() -> None:
        raise AssertionError("shutdown must not create a scheduler")

    monkeypatch.setattr(scheduler_module, "get_scheduler", unexpected_scheduler_creation)
    monkeypatch.setattr(scheduler_module, "stop_scheduler", stop_scheduler)
    monkeypatch.setattr(scheduler_module, "release_scheduler_leader", release_leader)
    monkeypatch.setattr(spend_writer, "spend_queue", SpendQueue())
    monkeypatch.setattr(tool_factory, "close_tool_http_client", close_tool_client)
    monkeypatch.setattr(checkpointer, "shutdown_checkpointer", close_checkpointer)
    monkeypatch.setattr(database, "shutdown_database", dispose_database)

    # When the production shutdown sequence runs.
    errors = await runtime_lifecycle.shutdown_runtime_resources()

    # Then database disposal follows checkpointer shutdown despite its failure.
    assert calls == [
        "skill_worker",
        "run_registry",
        "scheduler_stop",
        "scheduler_release",
        "spend_queue",
        "tool_http_client",
        "checkpointer",
        "database",
    ]
    assert errors == [failure]
