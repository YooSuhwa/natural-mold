"""Tests for app.agent_runtime.middleware_registry — registry, build, provider."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.agent_runtime.middleware_registry import (
    DEEPAGENT_AUTO_INJECTED_TYPES,
    DEEPAGENT_BUILTIN_TYPES,
    EXPLICITLY_INSTANTIATED_TYPES,
    MIDDLEWARE_REGISTRY,
    _coerce_tuple_params,
    _resolve_middleware_class,
    build_middleware_instances,
    get_middleware_registry,
    get_provider_middleware,
)

# ---------------------------------------------------------------------------
# get_middleware_registry
# ---------------------------------------------------------------------------


def test_get_middleware_registry():
    """Returns full catalog with type keys."""
    result = get_middleware_registry()
    assert isinstance(result, list)
    assert len(result) == len(MIDDLEWARE_REGISTRY)
    # Each entry should have 'type' key
    types = {item["type"] for item in result}
    assert "summarization" in types
    assert "tool_retry" in types
    assert "anthropic_prompt_caching" in types


def test_get_middleware_registry_entries_have_metadata():
    """Each entry includes display_name, description, category."""
    result = get_middleware_registry()
    for item in result:
        assert "type" in item
        assert "display_name" in item
        assert "description" in item
        assert "category" in item


def test_model_call_limit_thread_default_is_100():
    entry = MIDDLEWARE_REGISTRY["model_call_limit"]

    assert entry["config_schema"]["thread_limit"]["default"] == 100


def test_get_middleware_registry_exclude_builtin_keeps_explicit_instantiated():
    """exclude_builtin=True excludes auto-injected types only.

    ``human_in_the_loop`` is explicitly instantiated by executor.py based on
    user-defined ``interrupt_on`` policy and MUST stay visible in the catalog
    so users can register it through the middleware add UI.
    """
    result = get_middleware_registry(exclude_builtin=True)
    types = {item["type"] for item in result}
    for auto_type in DEEPAGENT_AUTO_INJECTED_TYPES:
        assert auto_type not in types, f"auto-injected '{auto_type}' should be excluded"
    for explicit_type in EXPLICITLY_INSTANTIATED_TYPES:
        assert explicit_type in types, f"explicit-instantiated '{explicit_type}' should be exposed"


def test_deepagent_builtin_types_is_union_of_auto_and_explicit():
    """DEEPAGENT_BUILTIN_TYPES is the union — used by build-time filtering only."""
    assert DEEPAGENT_BUILTIN_TYPES == DEEPAGENT_AUTO_INJECTED_TYPES | EXPLICITLY_INSTANTIATED_TYPES
    assert "human_in_the_loop" in DEEPAGENT_BUILTIN_TYPES
    assert "human_in_the_loop" in EXPLICITLY_INSTANTIATED_TYPES
    assert "human_in_the_loop" not in DEEPAGENT_AUTO_INJECTED_TYPES


# ---------------------------------------------------------------------------
# _coerce_tuple_params
# ---------------------------------------------------------------------------


def test_coerce_tuple_params():
    """Lists in tuple-type params are converted to tuples."""
    config_schema = {
        "trigger": {"type": "tuple", "default": ["tokens", 4000]},
        "keep": {"type": "tuple", "default": ["messages", 20]},
    }
    params = {"trigger": ["tokens", 8000], "keep": ["messages", 10]}
    result = _coerce_tuple_params(params, config_schema)
    assert isinstance(result["trigger"], tuple)
    assert result["trigger"] == ("tokens", 8000)
    assert isinstance(result["keep"], tuple)


def test_coerce_tuple_params_no_conversion_needed():
    """Non-tuple params are not converted."""
    config_schema = {
        "max_retries": {"type": "integer", "default": 3},
    }
    params = {"max_retries": 5}
    result = _coerce_tuple_params(params, config_schema)
    assert result["max_retries"] == 5


# ---------------------------------------------------------------------------
# _resolve_middleware_class
# ---------------------------------------------------------------------------


def test_resolve_middleware_class_unknown_type():
    """Unknown middleware type returns None."""
    result = _resolve_middleware_class("totally_nonexistent_type_xyz")
    assert result is None


def test_resolve_middleware_class_import_failure():
    """When langchain.agents.middleware is not importable, returns None."""
    with patch("importlib.import_module", side_effect=ImportError("not installed")):
        result = _resolve_middleware_class("summarization")
        assert result is None


def test_resolve_middleware_class_llm_tool_selector():
    """llm_tool_selector uses patched class."""
    # This will return None if langchain.agents.middleware is not installed
    result = _resolve_middleware_class("llm_tool_selector")
    # Either None (not installed) or a class
    assert result is None or isinstance(result, type)


# ---------------------------------------------------------------------------
# build_middleware_instances
# ---------------------------------------------------------------------------


def test_build_middleware_instances_empty():
    """Empty configs returns empty list."""
    result = build_middleware_instances([])
    assert result == []


def test_build_middleware_instances_unknown_type():
    """Unknown middleware type is skipped."""
    result = build_middleware_instances([{"type": "nonexistent_xyz", "params": {}}])
    assert result == []


def test_build_middleware_instances_class_not_available():
    """When class can't be resolved, instance is skipped."""
    with patch(
        "app.agent_runtime.middleware_registry._resolve_middleware_class",
        return_value=None,
    ):
        result = build_middleware_instances([{"type": "summarization", "params": {}}])
        assert result == []


def test_build_middleware_instances_instantiation_error():
    """When constructor raises, instance is skipped."""
    mock_cls = MagicMock(side_effect=TypeError("bad args"))
    with patch(
        "app.agent_runtime.middleware_registry._resolve_middleware_class",
        return_value=mock_cls,
    ):
        result = build_middleware_instances([{"type": "summarization", "params": {}}])
        assert result == []


def test_build_middleware_instances_success():
    """When class resolves and instantiates, instance is returned."""
    mock_instance = MagicMock()
    mock_cls = MagicMock(return_value=mock_instance)
    with patch(
        "app.agent_runtime.middleware_registry._resolve_middleware_class",
        return_value=mock_cls,
    ):
        result = build_middleware_instances([{"type": "tool_retry", "params": {"max_retries": 5}}])
        assert len(result) == 1
        assert result[0] is mock_instance


def test_build_middleware_instances_applies_model_call_limit_default():
    mock_cls = MagicMock(return_value=MagicMock())
    with patch(
        "app.agent_runtime.middleware_registry._resolve_middleware_class",
        return_value=mock_cls,
    ):
        result = build_middleware_instances([{"type": "model_call_limit", "params": {}}])

    assert len(result) == 1
    assert mock_cls.call_args.kwargs["thread_limit"] == 100


# ---------------------------------------------------------------------------
# get_provider_middleware
# ---------------------------------------------------------------------------


def test_get_provider_middleware_unknown():
    """Unknown provider returns empty list."""
    result = get_provider_middleware("unknown_provider")
    assert result == []


def test_get_provider_middleware_anthropic():
    """Anthropic provider returns list (may be empty if not installed)."""
    result = get_provider_middleware("anthropic")
    # Returns empty if langchain.agents.middleware not installed
    assert isinstance(result, list)


def test_get_provider_middleware_openai():
    """OpenAI provider returns list (may be empty if not installed)."""
    result = get_provider_middleware("openai")
    assert isinstance(result, list)
