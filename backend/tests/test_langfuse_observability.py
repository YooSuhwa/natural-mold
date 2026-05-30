from __future__ import annotations

from contextlib import nullcontext
from typing import Any

import pytest

from app.agent_runtime.executor import AgentConfig


def _cfg(**overrides: Any) -> AgentConfig:
    base: dict[str, Any] = {
        "provider": "openai",
        "model_name": "gpt-4o",
        "api_key": "sk-test",
        "base_url": None,
        "system_prompt": "You are helpful.",
        "tools_config": [],
        "thread_id": "conv-123",
        "agent_id": "agent-123",
        "user_id": "user-123",
        "model_id": "model-123",
        "checkpoint_id": "checkpoint-123",
    }
    base.update(overrides)
    return AgentConfig(**base)


def test_langfuse_context_disabled_when_flag_off(monkeypatch) -> None:
    from app.config import settings
    from app.observability.langfuse import build_langfuse_run_context

    monkeypatch.setattr(settings, "langfuse_enabled", False, raising=False)

    ctx = build_langfuse_run_context(
        _cfg(),
        run_id="run-123",
        source="chat",
    )

    assert ctx.enabled is False
    assert ctx.trace is None
    assert ctx.configure_config({"configurable": {"thread_id": "conv-123"}}) == {
        "configurable": {"thread_id": "conv-123"}
    }
    with ctx.activate(input_payload={"messages": ["hi"]}, output_payload=None):
        pass


def test_langfuse_context_auto_enables_when_connection_env_exists(monkeypatch) -> None:
    from app.config import settings
    from app.observability import langfuse as langfuse_obs

    class FakeClient:
        def create_trace_id(self, seed: str) -> str:
            return f"{seed.replace('-', ''):0<32}"[:32]

        def start_as_current_observation(self, **_kwargs: Any):
            return nullcontext()

    class FakeHandler:
        pass

    monkeypatch.setattr(settings, "langfuse_enabled", None, raising=False)
    monkeypatch.setattr(settings, "langfuse_public_key", "pk-lf-test", raising=False)
    monkeypatch.setattr(settings, "langfuse_secret_key", "sk-lf-test", raising=False)
    monkeypatch.setattr(settings, "langfuse_base_url", "https://langfuse.local", raising=False)
    monkeypatch.setattr(langfuse_obs, "_get_langfuse_client", lambda: FakeClient())
    monkeypatch.setattr(langfuse_obs, "_build_callback_handler", lambda: FakeHandler())

    ctx = langfuse_obs.build_langfuse_run_context(_cfg(), run_id="run-123", source="chat")

    assert langfuse_obs.is_langfuse_enabled() is True
    assert ctx.enabled is True
    assert ctx.trace is not None
    assert ctx.trace.provider == "langfuse"


def test_langfuse_context_builds_metadata_and_trace_record(monkeypatch) -> None:
    from app.config import settings
    from app.observability import langfuse as langfuse_obs

    class FakeClient:
        def create_trace_id(self, seed: str) -> str:
            return f"{seed.replace('-', ''):0<32}"[:32]

        def get_trace_url(self, *, trace_id: str) -> str:
            return f"https://langfuse.local/project/moldy/traces/{trace_id}"

        def start_as_current_observation(self, **_kwargs: Any):
            return nullcontext()

    class FakeHandler:
        pass

    monkeypatch.setattr(settings, "langfuse_enabled", True, raising=False)
    monkeypatch.setattr(settings, "langfuse_public_key", "pk-lf-test", raising=False)
    monkeypatch.setattr(settings, "langfuse_secret_key", "sk-lf-test", raising=False)
    monkeypatch.setattr(settings, "langfuse_base_url", "https://langfuse.local", raising=False)
    monkeypatch.setattr(settings, "langfuse_sample_rate", 1.0, raising=False)
    monkeypatch.setattr(langfuse_obs, "_get_langfuse_client", lambda: FakeClient())
    monkeypatch.setattr(langfuse_obs, "_build_callback_handler", lambda: FakeHandler())

    ctx = langfuse_obs.build_langfuse_run_context(
        _cfg(),
        run_id="run-123",
        source="regenerate",
    )

    assert ctx.enabled is True
    assert ctx.trace is not None
    assert ctx.trace.provider == "langfuse"
    assert ctx.trace.trace_id == "run12300000000000000000000000000"
    assert ctx.metadata["langfuse_user_id"] == "user-123"
    assert ctx.metadata["langfuse_session_id"] == "conv-123"
    assert ctx.metadata["moldy_agent_id"] == "agent-123"
    assert ctx.metadata["moldy_run_id"] == "run-123"
    assert ctx.metadata["moldy_source"] == "regenerate"

    config = ctx.configure_config({"configurable": {"thread_id": "conv-123"}})
    assert config["configurable"] == {"thread_id": "conv-123"}
    assert config["callbacks"] and isinstance(config["callbacks"][0], FakeHandler)
    assert config["metadata"]["moldy_conversation_id"] == "conv-123"
    assert "source:regenerate" in config["tags"]


def test_langfuse_context_init_failure_is_noop(monkeypatch) -> None:
    from app.config import settings
    from app.observability import langfuse as langfuse_obs

    monkeypatch.setattr(settings, "langfuse_enabled", True, raising=False)
    monkeypatch.setattr(settings, "langfuse_public_key", "pk-lf-test", raising=False)
    monkeypatch.setattr(settings, "langfuse_secret_key", "sk-lf-test", raising=False)
    monkeypatch.setattr(settings, "langfuse_base_url", "https://langfuse.local", raising=False)
    monkeypatch.setattr(
        langfuse_obs, "_get_langfuse_client", lambda: (_ for _ in ()).throw(RuntimeError("down"))
    )

    ctx = langfuse_obs.build_langfuse_run_context(_cfg(), run_id="run-123", source="chat")

    assert ctx.enabled is False
    assert ctx.trace is None


@pytest.mark.asyncio
async def test_fetch_observations_falls_back_to_legacy_trace_api(monkeypatch) -> None:
    from app.config import settings
    from app.observability import langfuse as langfuse_obs

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict[str, Any]):
            self.status_code = status_code
            self._payload = payload

        @property
        def is_success(self) -> bool:
            return 200 <= self.status_code < 300

        def json(self) -> dict[str, Any]:
            return self._payload

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url: str, **_kwargs: Any) -> FakeResponse:
            if url.endswith("/api/public/v2/observations"):
                return FakeResponse(501, {"message": "Not Implemented"})
            if "/api/public/traces/" in url:
                return FakeResponse(
                    200,
                    {
                        "observations": [
                            {
                                "id": "obs-1",
                                "name": "ChatOpenAI",
                                "type": "GENERATION",
                                "level": "DEFAULT",
                            }
                        ]
                    },
                )
            raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(settings, "langfuse_enabled", True, raising=False)
    monkeypatch.setattr(settings, "langfuse_public_key", "pk-lf-test", raising=False)
    monkeypatch.setattr(settings, "langfuse_secret_key", "sk-lf-test", raising=False)
    monkeypatch.setattr(settings, "langfuse_base_url", "https://langfuse.local", raising=False)
    monkeypatch.setattr(langfuse_obs.httpx, "AsyncClient", lambda **_kwargs: FakeClient())

    rows, error = await langfuse_obs.fetch_langfuse_observations("trace-123")

    assert error is None
    assert rows == [
        {
            "id": "obs-1",
            "name": "ChatOpenAI",
            "type": "GENERATION",
            "level": "DEFAULT",
            "traceId": "trace-123",
        }
    ]


@pytest.mark.asyncio
async def test_fetch_observations_hides_upstream_http_errors(monkeypatch) -> None:
    from app.config import settings
    from app.observability import langfuse as langfuse_obs

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict[str, Any]):
            self.status_code = status_code
            self._payload = payload

        @property
        def is_success(self) -> bool:
            return 200 <= self.status_code < 300

        def json(self) -> dict[str, Any]:
            return self._payload

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url: str, **_kwargs: Any) -> FakeResponse:
            if url.endswith("/api/public/v2/observations"):
                return FakeResponse(501, {"message": "Not Implemented"})
            if "/api/public/traces/" in url:
                return FakeResponse(404, {"message": "Not Found"})
            if url.endswith("/api/public/observations"):
                return FakeResponse(404, {"message": "Not Found"})
            raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(settings, "langfuse_enabled", True, raising=False)
    monkeypatch.setattr(settings, "langfuse_public_key", "pk-lf-test", raising=False)
    monkeypatch.setattr(settings, "langfuse_secret_key", "sk-lf-test", raising=False)
    monkeypatch.setattr(settings, "langfuse_base_url", "https://langfuse.local", raising=False)
    monkeypatch.setattr(langfuse_obs.httpx, "AsyncClient", lambda **_kwargs: FakeClient())

    rows, error = await langfuse_obs.fetch_langfuse_observations("trace-123")

    assert rows == []
    assert error == "Langfuse observations unavailable; showing local trace events fallback"
    assert "501" not in error
    assert "/api/public/v2/observations" not in error
