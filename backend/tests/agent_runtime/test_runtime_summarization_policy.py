"""Exact construction tests for stored runtime summarization policy."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from deepagents.backends import StateBackend
from langchain_core.language_models import ModelProfile
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.agent_runtime.offload_storage import ScopedOffloadBackend
from app.agent_runtime.offload_storage_types import OffloadIdentity
from app.agent_runtime.runtime_policy import (
    ASSISTANT_RUNTIME_POLICY,
    LEGACY_RUNTIME_POLICY,
    resolve_runtime_policy,
)
from app.agent_runtime.runtime_summarization_policy import (
    RuntimeSummarizationPolicyError,
    create_stored_summarization_middleware,
)


def _stored_summarization_policy(summarization: dict[str, Any]):
    return resolve_runtime_policy(
        {
            "version": 1,
            "summarization": summarization,
        }
    )


def _model(profile: dict[str, Any] | None) -> FakeListChatModel:
    return FakeListChatModel(
        responses=["done"],
        profile=cast(ModelProfile | None, profile),
    )


def _model_with_raw_profile(profile: Any) -> FakeListChatModel:
    model = _model(None)
    object.__setattr__(model, "profile", profile)
    return model


@pytest.mark.parametrize(
    ("profile", "uses_profile_defaults"),
    [
        ({"max_input_tokens": 128_000}, True),
        ({"max_input_tokens": 0}, True),
        ({"max_input_tokens": -1}, True),
        ({"max_input_tokens": True}, True),
        (None, False),
        ({}, False),
        ({"max_input_tokens": "128000"}, False),
        ({"max_input_tokens": 1.5}, False),
    ],
)
def test_auto_uses_complete_deepagents_defaults_without_overriding_omitted_keys(
    profile: dict[str, Any] | None,
    uses_profile_defaults: bool,
) -> None:
    model = _model_with_raw_profile(profile)
    backend = StateBackend()

    middleware = create_stored_summarization_middleware(
        _stored_summarization_policy({"mode": "auto"}),
        model,
        backend,
    )

    assert middleware.model is model
    assert middleware._backend is backend
    expected_trigger = ("fraction", 0.85) if uses_profile_defaults else ("tokens", 170_000)
    expected_keep = ("fraction", 0.10) if uses_profile_defaults else ("messages", 6)
    expected_truncate_trigger = ("fraction", 0.85) if uses_profile_defaults else ("messages", 20)
    expected_truncate_keep = ("fraction", 0.10) if uses_profile_defaults else ("messages", 20)
    assert middleware._lc_helper.trigger == expected_trigger
    assert middleware._lc_helper.keep == expected_keep
    assert middleware._lc_helper.trim_tokens_to_summarize is None
    assert middleware._truncate_args_trigger == expected_truncate_trigger
    assert middleware._truncate_args_keep == expected_truncate_keep
    assert middleware._max_arg_length == 2000
    assert middleware._truncation_text == "...(argument truncated)"


def test_auto_passes_complete_upstream_mapping_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent_runtime import runtime_summarization_policy as policy_module

    model = _model(None)
    backend = StateBackend()
    truncate_settings = {
        "trigger": ("messages", 31),
        "keep": ("messages", 7),
    }
    defaults = {
        "trigger": ("tokens", 1234),
        "keep": ("messages", 7),
        "truncate_args_settings": truncate_settings,
    }
    sentinel = object()
    constructor = MagicMock(return_value=sentinel)
    monkeypatch.setattr(policy_module, "compute_summarization_defaults", lambda _model: defaults)
    monkeypatch.setattr(policy_module, "SummarizationMiddleware", constructor)

    result = policy_module.create_stored_summarization_middleware(
        _stored_summarization_policy({"mode": "auto"}),
        model,
        backend,
    )

    assert result is sentinel
    constructor.assert_called_once_with(
        model=model,
        backend=backend,
        trim_tokens_to_summarize=None,
        trigger=defaults["trigger"],
        keep=defaults["keep"],
        truncate_args_settings=truncate_settings,
    )


def test_balanced_preset_hydrates_every_exact_constructor_value() -> None:
    model = _model({"max_input_tokens": 128_000})
    backend = StateBackend()

    middleware = create_stored_summarization_middleware(
        _stored_summarization_policy({"mode": "preset", "preset": "balanced_context_v1"}),
        model,
        backend,
    )

    assert middleware.model is model
    assert middleware._backend is backend
    assert middleware._lc_helper.trigger == ("fraction", 0.80)
    assert middleware._lc_helper.keep == ("fraction", 0.15)
    assert middleware._lc_helper.trim_tokens_to_summarize is None
    assert middleware._truncate_args_trigger == ("fraction", 0.70)
    assert middleware._truncate_args_keep == ("fraction", 0.10)
    assert middleware._max_arg_length == 2000
    assert middleware._truncation_text == "...(truncated)"


@pytest.mark.parametrize(
    "profile",
    [None, {}, {"max_input_tokens": 0}, {"max_input_tokens": -1}],
)
def test_balanced_preset_rejects_missing_or_nonpositive_profile(
    profile: dict[str, Any] | None,
) -> None:
    with pytest.raises(RuntimeSummarizationPolicyError) as exc_info:
        create_stored_summarization_middleware(
            _stored_summarization_policy({"mode": "preset", "preset": "balanced_context_v1"}),
            _model(profile),
            StateBackend(),
        )

    assert str(exc_info.value) == "RUNTIME_SUMMARIZATION_POLICY_INVALID"


@pytest.mark.parametrize("value", [True, 1.5, "128000"])
def test_balanced_preset_rejects_non_integer_profile_without_reflection(value: Any) -> None:
    model = _model(None)
    object.__setattr__(model, "profile", {"max_input_tokens": value})

    with pytest.raises(RuntimeSummarizationPolicyError) as exc_info:
        create_stored_summarization_middleware(
            _stored_summarization_policy({"mode": "preset", "preset": "balanced_context_v1"}),
            model,
            StateBackend(),
        )

    assert str(exc_info.value) == "RUNTIME_SUMMARIZATION_POLICY_INVALID"
    assert str(value) not in str(exc_info.value)


@pytest.mark.parametrize("policy", [LEGACY_RUNTIME_POLICY, ASSISTANT_RUNTIME_POLICY])
def test_hydrator_rejects_nonstored_policy(policy: Any) -> None:
    with pytest.raises(RuntimeSummarizationPolicyError) as exc_info:
        create_stored_summarization_middleware(policy, _model(None), StateBackend())

    assert str(exc_info.value) == "RUNTIME_SUMMARIZATION_POLICY_INVALID"


@patch("app.agent_runtime.runtime_component_builder.create_deep_agent")
def test_stored_policy_replaces_parent_only_and_preserves_actor_scoped_child_auto(
    mock_create: MagicMock,
    tmp_path: Path,
) -> None:
    from app.agent_runtime.runtime_component_builder import build_agent

    parent_model = _model({"max_input_tokens": 128_000})
    child_model = _model({"max_input_tokens": 64_000})
    backend = ScopedOffloadBackend(
        tmp_path,
        OffloadIdentity(owner_id="owner-a", conversation_id="thread-a", run_id="run-a"),
        uuid.uuid4(),
    )
    colliding_parent = MagicMock(name="caller-parent-summarizer")
    colliding_parent.name = "SummarizationMiddleware"
    colliding_child = MagicMock(name="caller-child-summarizer")
    colliding_child.name = "SummarizationMiddleware"

    build_agent(
        parent_model,
        [],
        "prompt",
        backend=backend,
        middleware=[colliding_parent],
        subagents=[
            {
                "name": "child",
                "description": "helper",
                "system_prompt": "help",
                "model": child_model,
                "middleware": [colliding_child],
                "_moldy_actor_id": uuid.uuid4(),
            }
        ],
        runtime_policy=_stored_summarization_policy(
            {"mode": "preset", "preset": "balanced_context_v1"}
        ),
    )

    call = mock_create.call_args.kwargs
    parent_summaries = [
        item for item in call["middleware"] if item.name == "SummarizationMiddleware"
    ]
    assert len(parent_summaries) == 1
    assert parent_summaries[0] is not colliding_parent
    assert parent_summaries[0].model is parent_model
    assert parent_summaries[0]._backend is backend
    assert parent_summaries[0]._lc_helper.trigger == ("fraction", 0.80)
    assert parent_summaries[0]._lc_helper.keep == ("fraction", 0.15)

    by_name = {spec["name"]: spec for spec in call["subagents"]}
    assert set(by_name) == {"general-purpose", "child"}
    for name, expected_model in (
        ("general-purpose", parent_model),
        ("child", child_model),
    ):
        summaries = [
            item for item in by_name[name]["middleware"] if item.name == "SummarizationMiddleware"
        ]
        assert len(summaries) == 1
        assert summaries[0].model is expected_model
        assert summaries[0]._backend is not backend
        assert summaries[0]._backend.artifacts_root != backend.artifacts_root
        assert summaries[0]._lc_helper.trigger == ("fraction", 0.85)
        assert summaries[0]._lc_helper.keep == ("fraction", 0.10)
    assert colliding_child not in by_name["child"]["middleware"]


@patch("app.agent_runtime.runtime_component_builder.create_deep_agent")
def test_nonstored_manifest_remains_legacy_compatible(mock_create: MagicMock) -> None:
    from app.agent_runtime.runtime_component_builder import build_agent

    caller_summarizer = MagicMock()
    caller_summarizer.name = "SummarizationMiddleware"
    build_agent(
        _model(None),
        [],
        "prompt",
        middleware=[caller_summarizer],
        runtime_policy=LEGACY_RUNTIME_POLICY,
    )

    assert mock_create.call_args.kwargs["middleware"][-1] is caller_summarizer
