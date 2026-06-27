"""compute_usage_timing 단위 테스트 (TTFT / 총시간 / tok-s).

``compute_usage_timing`` 은 내부에서 ``time.monotonic()`` 으로 now 를 잡으므로,
입력 시각은 실제 monotonic 기준 상대값으로 앵커링해야 generation/tok-s 가
현실적인 값이 된다(고정 상수면 now 와의 간격이 수천 초라 tok-s 가 0 으로 반올림).
"""

from __future__ import annotations

import time

import pytest

from app.agent_runtime.usage_timing import compute_usage_timing


def test_includes_ttft_and_tokens_per_second_when_available() -> None:
    now = time.monotonic()
    # 시작 1.0s 전, 첫 토큰 0.5s 전 → TTFT 500ms, 생성 ~1s, tok/s ~ 50/0.5 = 100.
    timing = compute_usage_timing(
        started_at=now - 1.0, first_token_at=now - 0.5, completion_tokens=50
    )
    # TTFT = (first - started) 는 now 와 무관하게 정확.
    assert timing["ttft_ms"] == pytest.approx(500.0, abs=1.0)
    assert timing["generation_ms"] == pytest.approx(1000.0, abs=50.0)
    assert timing["tokens_per_second"] == pytest.approx(100.0, rel=0.2)


def test_omits_ttft_when_no_first_token() -> None:
    now = time.monotonic()
    timing = compute_usage_timing(started_at=now - 0.5, first_token_at=None, completion_tokens=0)
    assert "ttft_ms" not in timing
    assert timing["generation_ms"] > 0  # 총시간은 항상 측정


def test_omits_tokens_per_second_when_no_completion() -> None:
    now = time.monotonic()
    timing = compute_usage_timing(
        started_at=now - 1.0, first_token_at=now - 0.9, completion_tokens=0
    )
    assert "tokens_per_second" not in timing
    assert timing["ttft_ms"] == pytest.approx(100.0, abs=1.0)
