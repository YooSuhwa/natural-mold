"""Detector packs: find personal data and secrets in text (guardrail-minimum-plan.md §2).

A pack returns spans only. What happens to a span (block, redact, mask, hash, warn) is decided
by the policy and the middleware, not here.

- ``common``: secrets (the ``marketplace.secret_scan`` patterns) and language-neutral values
  (email, card, IP) from ko-pii.
- ``kr``: the remaining ko-pii labels (RRN, phone, account, address, name, ...) plus an RRN
  pattern without the checksum, because RRNs issued since 2020-10 have a random tail.
- ``jailbreak``: prompt-attack phrases in the user's message (Korean and English).

Presidio is not used: its card and IP recognizers end on ``\\b``, which never matches before a
Korean particle (``4111...로``), and the RRN pattern is the only piece the measurement needed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from ko_pii.detect import detect_all

from app.marketplace.secret_scan import SECRET_CONTENT_PATTERNS


@dataclass(frozen=True, slots=True)
class Finding:
    entity: str
    start: int
    end: int
    score: float
    pack: str


class DetectorPack(Protocol):
    name: str

    def detect(self, text: str) -> list[Finding]: ...


# re.ASCII so ``\b`` also holds between a key and a Korean particle (``sk-...를``).
_SECRET_PATTERNS = tuple(
    re.compile(p.pattern.decode(), (p.flags & re.IGNORECASE) | re.ASCII)
    for p in SECRET_CONTENT_PATTERNS
)
_COMMON_LABELS = frozenset({"EMAIL", "CARD", "IP"})
# Same pattern as Presidio's KrRrnRecognizer: date-shaped front, gender digit 1-4, no checksum.
_RRN_PATTERN = re.compile(r"(?<!\d)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])-?[1-4]\d{6}(?!\d)")


def _ko_pii(
    text: str,
    pack: str,
    *,
    include: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
) -> list[Finding]:
    return [
        Finding(d.label, d.start, d.end, float(d.confidence), pack)
        for d in detect_all(text, include=include, exclude=exclude)
    ]


class CommonPack:
    name = "common"

    def detect(self, text: str) -> list[Finding]:
        found = [
            Finding("SECRET", m.start(), m.end(), 1.0, self.name)
            for pattern in _SECRET_PATTERNS
            for m in pattern.finditer(text)
        ]
        return found + _ko_pii(text, self.name, include=_COMMON_LABELS)


def rrn_fallback(text: str, pack: str) -> list[Finding]:
    return [Finding("RRN", m.start(), m.end(), 0.5, pack) for m in _RRN_PATTERN.finditer(text)]


class KoreanRulesPack:
    name = "kr"

    def detect(self, text: str) -> list[Finding]:
        return _ko_pii(text, self.name, exclude=_COMMON_LABELS) + rrn_fallback(text, self.name)


# Prompt attacks in the user's message (①). Phrases, not single words, so ordinary
# questions about prompts or rules are not flagged.
_JAILBREAK_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"(이전|위의?|앞의|앞서|모든|기존)\s*(의\s*)?(지시|지침|명령|규칙|프롬프트|설정)(사항)?"
        r"(을|를|은|는|들을|들은)?\s*(전부\s*|모두\s*|다\s*)?(무시|잊어|잊고|따르지\s*마)",
        r"(시스템|system)\s*(프롬프트|prompt|지시문)(을|를)?\s*(그대로\s*)?"
        r"(보여|알려|출력|공개|말해|복사)",
        r"(너는|넌|당신은)\s*이제\s*(아무\s*)?(제한|규칙|검열|제약)(이|은|도)?\s*없",
        r"(개발자|관리자)\s*모드(로|를)?\s*(전환|켜|활성)",
        r"탈옥",
        r"\b(ignore|disregard|forget)\s+(all\s+|any\s+)?(of\s+)?(the\s+|your\s+)?"
        r"(previous|prior|above|earlier|preceding)\s+(instructions|prompts|rules|directions)",
        r"\b(reveal|show|print|repeat|output)\s+(me\s+)?(your|the)\s+"
        r"(system\s+prompt|hidden\s+instructions|initial\s+instructions)",
        r"\byou\s+are\s+now\s+(DAN|unrestricted|jailbroken|free\s+of\s+all\s+rules)",
        r"\b(jailbreak|DAN\s+mode|developer\s+mode\s+enabled)\b",
    )
)


class JailbreakPack:
    name = "jailbreak"

    def detect(self, text: str) -> list[Finding]:
        return [
            Finding("JAILBREAK", m.start(), m.end(), 1.0, self.name)
            for pattern in _JAILBREAK_PATTERNS
            for m in pattern.finditer(text)
        ]


PACKS: dict[str, DetectorPack] = {
    pack.name: pack for pack in (CommonPack(), KoreanRulesPack(), JailbreakPack())
}


def merge(findings: Iterable[Finding]) -> list[Finding]:
    """One finding per (entity, span), highest score kept, in text order."""
    best: dict[tuple[str, int, int], Finding] = {}
    for finding in findings:
        key = (finding.entity, finding.start, finding.end)
        if key not in best or finding.score > best[key].score:
            best[key] = finding
    return sorted(best.values(), key=lambda f: (f.start, f.end, f.entity))


def detect(text: str, packs: Sequence[str]) -> list[Finding]:
    """Run the named rule packs. The ``kr_model`` pack is async: ``model_pack.detect_texts``."""
    return merge(finding for name in packs for finding in PACKS[name].detect(text))
