"""The old catalog PII middleware steps aside where guardrails check the same value (P1)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.agent_runtime.middleware_registry import get_middleware_registry, is_superseded
from app.guardrails.legacy import covered_by_guardrails
from app.guardrails.rules import BUILTIN_TEMPLATES
from app.services.builder_session_service import _get_middlewares_catalog
from tests.test_executor import _cfg

KR_DEFAULT = BUILTIN_TEMPLATES["kr-default"]


def _pii(pii_type: str) -> dict[str, Any]:
    return {"type": "pii", "params": {"pii_type": pii_type, "strategy": "redact"}}


@pytest.mark.parametrize(
    ("config", "rules", "covered"),
    [
        (_pii("email"), KR_DEFAULT, True),  # contacts rule masks EMAIL before sending
        (_pii("credit_card"), BUILTIN_TEMPLATES["common-basic"], True),
        (_pii("mac_address"), KR_DEFAULT, False),  # guardrails do not detect it
        (_pii("email"), None, False),  # guardrails off: the old middleware stays
        (_pii("ip"), KR_DEFAULT, False),  # kr-default has no IP rule
        ({"type": "tool_retry", "params": {}}, KR_DEFAULT, False),
    ],
)
def test_coverage(config: dict[str, Any], rules: Any, covered: bool) -> None:
    assert covered_by_guardrails(config, rules) is covered


def test_new_agents_are_not_offered_the_old_pii_middleware() -> None:
    assert is_superseded("pii") and not is_superseded("tool_retry")
    catalog = {entry["type"]: entry for entry in get_middleware_registry(exclude_builtin=True)}
    assert catalog["pii"]["superseded_by"] == "guardrails"  # existing agents still show it
    assert "pii" not in {entry["type"] for entry in _get_middlewares_catalog()}


async def _built_middleware(**cfg: Any) -> list[str]:
    from app.agent_runtime.agent_stream_runner import execute_agent_stream

    async def fake_stream(*_args: Any, **_kwargs: Any) -> Any:
        yield "done"

    with (
        patch("app.agent_runtime.checkpointer.get_checkpointer"),
        patch("app.agent_runtime.agent_stream_runner.stream_agent_response") as stream,
        patch("app.agent_runtime.runtime_component_builder.build_agent") as build,
        patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages"),
        patch("app.agent_runtime.runtime_component_builder.create_chat_model") as model,
    ):
        model.return_value = MagicMock()
        build.return_value = MagicMock()
        stream.return_value = fake_stream()
        async for _ in execute_agent_stream(_cfg(**cfg), []):
            pass
    return [type(item).__name__ for item in build.call_args[1]["middleware"] or []]


async def test_a_guarded_run_drops_the_covered_middleware_and_keeps_the_rest() -> None:
    names = await _built_middleware(
        middleware_configs=[_pii("email"), _pii("mac_address")],
        guardrail_rules=KR_DEFAULT,
        guardrail_resolved=True,
    )

    assert names.count("PIIMiddleware") == 1  # mac_address only


async def test_without_guardrails_the_old_middleware_runs_as_configured() -> None:
    names = await _built_middleware(
        middleware_configs=[_pii("email")], guardrail_rules=None, guardrail_resolved=True
    )

    assert names.count("PIIMiddleware") == 1
