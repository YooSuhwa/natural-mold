from __future__ import annotations

from unittest.mock import MagicMock

from app.agent_runtime.token_tracker import TokenTrackingCallback


def _make_llm_result(token_usage: dict | None = None, llm_output: dict | None = ...):
    """Build a mock LLMResult with the given token_usage."""
    result = MagicMock()
    if llm_output is ...:
        result.llm_output = {"token_usage": token_usage} if token_usage is not None else None
    else:
        result.llm_output = llm_output
    return result


class TestTokenTrackingCallback:
    async def test_on_llm_end_accumulates_tokens(self):
        cb = TokenTrackingCallback()
        result = _make_llm_result(
            {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
        )
        await cb.on_llm_end(result)

        assert cb.prompt_tokens == 10
        assert cb.completion_tokens == 20
        assert cb.total_tokens == 30

    async def test_accumulation_across_multiple_calls(self):
        cb = TokenTrackingCallback()
        r1 = _make_llm_result({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        r2 = _make_llm_result({"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30})
        await cb.on_llm_end(r1)
        await cb.on_llm_end(r2)

        assert cb.prompt_tokens == 30
        assert cb.completion_tokens == 15
        assert cb.total_tokens == 45

    async def test_get_usage_returns_correct_dict(self):
        cb = TokenTrackingCallback()
        result = _make_llm_result(
            {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        )
        await cb.on_llm_end(result)

        usage = cb.get_usage()
        assert usage == {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }

    async def test_missing_token_usage_in_llm_output(self):
        cb = TokenTrackingCallback()
        # llm_output exists but has no "token_usage" key
        result = _make_llm_result(llm_output={"model_name": "gpt-4o"})
        await cb.on_llm_end(result)

        assert cb.get_usage() == {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    async def test_none_llm_output(self):
        cb = TokenTrackingCallback()
        result = _make_llm_result(llm_output=None)
        await cb.on_llm_end(result)

        assert cb.get_usage() == {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    async def test_partial_token_usage(self):
        cb = TokenTrackingCallback()
        # Only prompt_tokens provided
        result = _make_llm_result({"prompt_tokens": 42})
        await cb.on_llm_end(result)

        assert cb.prompt_tokens == 42
        assert cb.completion_tokens == 0
        assert cb.total_tokens == 0
