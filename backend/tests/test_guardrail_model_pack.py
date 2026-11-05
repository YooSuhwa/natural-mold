"""The ``kr_model`` pack client: the pii-detector service answers or the rules pack does.

The service itself (docker/pii-detector) is checked live: docs/design-docs/
korean-pii-detector-benchmark.md, "Moldy 탐지 묶음". Here a fake transport stands in for it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

import httpx
import pytest

from app.config import settings
from app.guardrails import model_pack
from app.guardrails.model_pack import MODEL_PACK, detect_texts

TEXT = "담당자 김민수 과장의 번호는 880315-3123456입니다"


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[httpx.Request]]:
    """Route the pack's HTTP client to ``handler``; returns the requests it received."""

    def install(handler: Callable[[httpx.Request], httpx.Response], *, key: str = "") -> list:
        seen: list[httpx.Request] = []

        def record(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        real = model_pack.outbound_http_client
        monkeypatch.setattr(settings, "guardrail_pii_model_url", "http://pii-detector:8095")
        monkeypatch.setattr(settings, "guardrail_pii_model_api_key", key)
        monkeypatch.setattr(model_pack, "_service_down", False)
        monkeypatch.setattr(
            model_pack,
            "outbound_http_client",
            lambda **kw: real(transport=httpx.MockTransport(record), **kw),
        )
        return seen

    return install


def _person_reply(request: httpx.Request) -> httpx.Response:
    texts = json.loads(request.content)["texts"]
    results = [
        [
            {
                "entity": "PERSON",
                "start": t.find("김민수"),
                "end": t.find("김민수") + 3,
                "score": 0.99,
            }
        ]
        if "김민수" in t
        else []
        for t in texts
    ]
    return httpx.Response(200, json={"model": "test", "results": results})


async def test_without_a_service_the_rules_pack_answers() -> None:
    assert settings.guardrail_pii_model_url == ""

    [findings] = await detect_texts([TEXT], [MODEL_PACK])

    assert {(f.entity, f.pack) for f in findings} >= {("RRN", "kr")}
    assert all(f.pack == "kr" for f in findings)


async def test_service_findings_get_the_rrn_fallback(service) -> None:
    seen = service(_person_reply)

    [findings] = await detect_texts([TEXT], ["common", MODEL_PACK])

    spans = {(f.entity, TEXT[f.start : f.end], f.pack) for f in findings}
    assert spans == {("PERSON", "김민수", MODEL_PACK), ("RRN", "880315-3123456", MODEL_PACK)}
    assert len(seen) == 1
    assert "Authorization" not in seen[0].headers
    # Korean goes as UTF-8, not \\u escapes (the service's 1 MiB limit counts bytes).
    assert "김민수".encode() in seen[0].content


async def test_api_key_is_sent_when_set(service) -> None:
    seen = service(_person_reply, key="k-123")

    await detect_texts([TEXT], [MODEL_PACK])

    assert seen[0].headers["Authorization"] == "Bearer k-123"


@pytest.mark.parametrize(
    "reply",
    [
        lambda _r: httpx.Response(503, json={"error": "model_missing"}),
        lambda _r: httpx.Response(200, json={"results": []}),
        lambda _r: httpx.Response(
            200, json={"results": [[{"entity": "PERSON", "start": 0, "end": 999, "score": 1}]]}
        ),
        lambda _r: httpx.Response(200, content=b"not json"),
    ],
    ids=["unavailable", "wrong-count", "span-outside", "not-json"],
)
async def test_a_bad_answer_falls_back_to_the_rules_pack(service, reply) -> None:
    service(reply)

    [findings] = await detect_texts([TEXT], [MODEL_PACK])

    assert findings
    assert {f.pack for f in findings} == {"kr"}


async def test_unavailable_is_logged_once_without_the_text(
    service, caplog: pytest.LogCaptureFixture
) -> None:
    service(lambda _r: httpx.Response(503, json={"error": "model_missing"}))

    with caplog.at_level(logging.WARNING, logger=model_pack.__name__):
        await detect_texts([TEXT], [MODEL_PACK])
        await detect_texts([TEXT], [MODEL_PACK])

    assert [r.getMessage() for r in caplog.records] == [
        "guardrail model pack unavailable, using the kr rules pack: HTTPStatusError"
    ]
    assert "김민수" not in caplog.text


async def test_texts_are_batched_and_an_oversized_text_uses_the_rules_pack(service) -> None:
    seen = service(_person_reply)
    huge = TEXT + "가" * (1 << 19)  # over 1 MiB as UTF-8
    texts = [TEXT] * 70 + [huge]

    results = await detect_texts(texts, [MODEL_PACK])

    assert [len(json.loads(r.content)["texts"]) for r in seen] == [64, 6]
    assert {f.pack for f in results[0]} == {MODEL_PACK}
    assert {f.pack for f in results[-1]} == {"kr"}
