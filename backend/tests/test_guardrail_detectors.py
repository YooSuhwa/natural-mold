"""Guardrail detector packs (guardrail-minimum-plan.md §2): a small Korean regression set.

All values are synthetic. The full measurement is poc/korean-pii-bench/bench_packs.py
(docs/design-docs/korean-pii-detector-benchmark.md); this set keeps the cases that matter
for Korean text: values followed by particles, no separators, and look-alikes that must pass.
"""

from __future__ import annotations

import time

import pytest

from app.guardrails import detectors
from app.guardrails.detectors import detect

PACKS = ["common", "kr"]

MUST_DETECT = [
    ("RRN", "900101-1234567", "주민등록번호 900101-1234567로 신청했습니다"),
    ("RRN", "0303154123456", "주민번호는 0303154123456이에요"),
    ("PHONE", "010-1234-5678", "제 번호는 010-1234-5678입니다"),
    ("PHONE", "01012345678", "연락처 01012345678로 문자 주세요"),
    ("EMAIL", "hong.gildong@example.co.kr", "hong.gildong@example.co.kr로 보내 주세요"),
    ("CARD", "4111 1111 1111 1111", "카드 4111 1111 1111 1111로 결제"),
    ("IP", "192.168.10.24", "서버 192.168.10.24에서 접속"),
    ("ACCOUNT", "123456-78-901234", "국민은행 123456-78-901234로 입금"),
    ("PASSPORT", "M12345678", "여권번호 M12345678을 적어 주세요"),
    ("DRIVER_LICENSE", "11-23-456789-01", "운전면허 11-23-456789-01"),
    ("ADDRESS", "서울특별시 강남구 테헤란로 123", "서울특별시 강남구 테헤란로 123에 삽니다"),
    ("PERSON", "홍길동", "담당자 홍길동 과장에게 전달"),
    ("SECRET", "sk-abcdefghijklmnopqrstuvwx", "API 키는 sk-abcdefghijklmnopqrstuvwx를 쓰세요"),
    (
        "SECRET",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "토큰 ghp_abcdefghijklmnopqrstuvwxyz0123456789입니다",
    ),
    ("SECRET", "postgres://moldy:hunter2@", "postgres://moldy:hunter2@db:5432/moldy 로 접속"),
]

MUST_PASS = [
    "버전 1.2.3에서 고쳤습니다",
    "주문번호 2024-1105-0001을 확인하세요",
    "예시 키 sk-example 은 동작하지 않습니다",
    "회의는 10시 30분, 3층 회의실입니다",
    "총 1,234,567원이 청구되었습니다",
    "파이썬 3.12와 Node 22를 씁니다",
    "{{$credentials.api_key}} 자리에 값을 넣으세요",
    "학번 20241234",
]


@pytest.mark.parametrize(("entity", "value", "text"), MUST_DETECT)
def test_detects_the_exact_span(entity: str, value: str, text: str) -> None:
    spans = {(f.entity, text[f.start : f.end]) for f in detect(text, PACKS)}

    assert (entity, value) in spans


@pytest.mark.parametrize("text", MUST_PASS)
def test_look_alikes_pass(text: str) -> None:
    assert detect(text, PACKS) == []


def test_rrn_that_ko_pii_rejects_is_still_found() -> None:
    # ko-pii drops this shape (gender digit 3 with an 88 birth year, failing checksum). Of the
    # 1,257 RRNs in the benchmark's 3,000-doc sample, 428 are found only by the fallback.
    text = "주민번호 880315-3123456을 확인"

    assert [(f.entity, f.pack) for f in detect(text, ["kr"])] == [("RRN", "kr")]
    assert [f.score for f in detect(text, ["kr"])] == [0.5]


def test_packs_split_the_labels() -> None:
    text = "홍길동 a@b.co sk-abcdefghijklmnopqrstuvwx"

    assert {f.entity for f in detect(text, ["common"])} == {"EMAIL", "SECRET"}
    assert {f.entity for f in detect(text, ["kr"])} == {"PERSON"}


def test_same_span_from_two_detectors_is_reported_once() -> None:
    # ko-pii and the fallback pattern both find this RRN.
    findings = detect("주민번호 900101-1234567", ["kr"])

    assert len(findings) == 1
    assert findings[0].score == 0.7


@pytest.mark.parametrize(
    "blob",
    ["sk-" + "a-" * 50_000, "a.b." * 25_000, "?token=" + "x" * 100_000, "a" * 100_000],
)
def test_secret_patterns_stay_linear_on_long_runs(blob: str) -> None:
    # The URL-userinfo pattern was quadratic on ``a.b.a.b…`` (3 s for 100 KB) before the
    # scheme cap; now each blob takes a few ms.
    started = time.perf_counter()
    for pattern in detectors._SECRET_PATTERNS:
        list(pattern.finditer(blob))

    assert time.perf_counter() - started < 0.5
