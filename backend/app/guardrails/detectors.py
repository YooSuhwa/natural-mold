"""Detector packs: find personal data and secrets in text (guardrail-minimum-plan.md §2).

A pack returns spans only. What happens to a span (block, redact, mask, hash, warn) is decided
by the policy and the middleware, not here.

- ``common``: secrets (the ``marketplace.secret_scan`` patterns) and language-neutral values
  (email, card, IP) from ko-pii.
- ``kr``: the remaining ko-pii labels (RRN, phone, account, address, name, ...) plus an RRN
  pattern without the checksum, because RRNs issued since 2020-10 have a random tail.

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


class KoreanRulesPack:
    name = "kr"

    def detect(self, text: str) -> list[Finding]:
        found = _ko_pii(text, self.name, exclude=_COMMON_LABELS)
        return found + [
            Finding("RRN", m.start(), m.end(), 0.5, self.name) for m in _RRN_PATTERN.finditer(text)
        ]


PACKS: dict[str, DetectorPack] = {pack.name: pack for pack in (CommonPack(), KoreanRulesPack())}


def detect(text: str, packs: Sequence[str]) -> list[Finding]:
    """Run the named packs; one finding per (entity, span), highest score kept, in text order."""
    best: dict[tuple[str, int, int], Finding] = {}
    for name in packs:
        for finding in PACKS[name].detect(text):
            key = (finding.entity, finding.start, finding.end)
            if key not in best or finding.score > best[key].score:
                best[key] = finding
    return sorted(best.values(), key=lambda f: (f.start, f.end, f.entity))
