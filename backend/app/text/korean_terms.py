"""Korean terms for keyword search (memory policy plan 15, 5.4-11).

Postgres ``simple`` text search sees "세션을" and "세션" as different words. Terms come from
the Nori morphological analyzer (OpenSearch ``analysis-nori``, Apache-2.0) when it runs:
``_analyze`` with an inline analyzer, so OpenSearch holds no index and no memory text. When
it is not configured, fails or answers slower than ``korean_analyze_timeout_ms``, a particle
and ending suffix list stands in. Every stored term set records its analyzer, so a query is
analyzed the same way as the text it is compared with.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Final

import httpx

from app.config import settings
from app.outbound.client import outbound_http_client

logger = logging.getLogger(__name__)

NORI_ANALYZER: Final = "nori"
FALLBACK_ANALYZER: Final = "suffix"
ANALYZERS: Final = (NORI_ANALYZER, FALLBACK_ANALYZER)

# Parts of speech Nori drops: endings, particles, affixes, punctuation, interjections,
# adverbs and determiners (what is left is nouns, verb and adjective stems, foreign words
# and numbers).
_NORI_STOPTAGS: Final = (
    "E",
    "IC",
    "J",
    "MAG",
    "MAJ",
    "MM",
    "SP",
    "SSC",
    "SSO",
    "SC",
    "SE",
    "SF",
    "SY",
    "XPN",
    "XSA",
    "XSN",
    "XSV",
    "UNA",
    "NA",
    "VCP",
    "VCN",
    "VSV",
)
_NORI_ANALYZER_BODY: Final = {
    "tokenizer": {"type": "nori_tokenizer", "decompound_mode": "mixed"},
    "filter": [{"type": "nori_part_of_speech", "stoptags": list(_NORI_STOPTAGS)}, "lowercase"],
}

# Longest first. A suffix is cut only when at least two characters stay (GBrain's rule),
# so "회의" and "평가" keep their last syllable.
_SUFFIXES: Final = tuple(
    sorted(
        {
            # endings
            "했습니다",
            "합니다",
            "입니다",
            "습니다",
            "했어요",
            "해요",
            "했다",
            "한다",
            "하다",
            "하는",
            "하고",
            "해서",
            "했던",
            "하던",
            "해줘",
            "이었다",
            "였다",
            "이다",
            "어요",
            "아요",
            "세요",
            "니다",
            # particles
            "에서부터",
            "으로부터",
            "로부터",
            "에게서",
            "한테서",
            "까지는",
            "부터는",
            "에서는",
            "에서도",
            "으로는",
            "으로도",
            "이라고",
            "이라는",
            "라고",
            "라는",
            "처럼",
            "보다",
            "마다",
            "조차",
            "마저",
            "까지",
            "부터",
            "에서",
            "에게",
            "한테",
            "께서",
            "으로",
            "와는",
            "과는",
            "이랑",
            "이나",
            "이며",
            "에는",
            "에도",
            "은",
            "는",
            "이",
            "가",
            "을",
            "를",
            "에",
            "의",
            "와",
            "과",
            "도",
            "만",
            "로",
            "랑",
            "나",
            "요",
        },
        key=len,
        reverse=True,
    )
)
_MIN_STEM: Final = 2
# These rarely end a noun, so one syllable may stay ("팀은" -> "팀").
_SHORT_STEM_PARTICLES: Final = frozenset({"은", "는", "을", "를"})
_STOPWORDS: Final = frozenset(
    {
        "그",
        "이",
        "저",
        "것",
        "좀",
        "더",
        "또",
        "및",
        "등",
        "수",
        "때",
        "제",
        "내",
        "나",
        "너",
        "우리",
        "저희",
        "그거",
        "이거",
        "저거",
        "뭐",
        "a",
        "an",
        "the",
        "to",
        "of",
        "and",
        "or",
        "is",
        "are",
        "in",
        "on",
        "for",
        "with",
        "it",
        "please",
    }
)
# A run of Hangul, or a run of Latin letters and digits: "세션PostgreSQL17" is two words.
_WORD: Final = re.compile(r"[가-힣]+|[A-Za-z0-9]+")
_MAX_TEXT: Final = 4000


@dataclass(frozen=True, slots=True)
class Terms:
    analyzer: str
    terms: tuple[str, ...]

    def joined(self) -> str:
        return " ".join(self.terms)


def _strip(word: str) -> str:
    for suffix in _SUFFIXES:
        if not word.endswith(suffix):
            continue
        stem = len(word) - len(suffix)
        if stem >= _MIN_STEM or (stem >= 1 and suffix in _SHORT_STEM_PARTICLES):
            return word[:stem]
    return word


def _unique(words: list[str]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for word in words:
        if word and word not in _STOPWORDS:
            seen.setdefault(word, None)
    return tuple(seen)


def fallback_terms(text: str) -> tuple[str, ...]:
    """Suffix-list terms (no network): the fallback and the migration's first pass."""
    words = []
    for match in _WORD.finditer(text[:_MAX_TEXT]):
        word = match.group(0).lower()
        if "가" <= word[0] <= "힣":
            if word in _SUFFIXES:  # a particle left alone ("…17로")
                continue
            word = _strip(word)
        words.append(word)
    return _unique(words)


_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        auth = (
            httpx.BasicAuth(settings.opensearch_username, settings.opensearch_password)
            if settings.opensearch_username
            else None
        )
        # One pooled client: a fresh connection per call would not fit the time limit.
        _client = outbound_http_client(auth=auth)
    return _client


async def nori_terms(text: str, *, timeout_ms: int | None = None) -> tuple[str, ...] | None:
    """Nori terms, or ``None`` when OpenSearch is off, failing or too slow."""
    url = settings.opensearch_url.rstrip("/")
    if not url:
        return None
    limit = (timeout_ms if timeout_ms is not None else settings.korean_analyze_timeout_ms) / 1000
    started = time.perf_counter()
    try:
        response = await _http().post(
            f"{url}/_analyze",
            json={**_NORI_ANALYZER_BODY, "text": text[:_MAX_TEXT]},
            timeout=limit,
        )
        response.raise_for_status()
        tokens = response.json().get("tokens", [])
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("nori analyze unavailable: %s", type(exc).__name__)
        return None
    logger.debug("nori analyze %d ms", round((time.perf_counter() - started) * 1000))
    return _unique([str(t.get("token", "")) for t in tokens if isinstance(t, dict)])


async def korean_terms(text: str) -> Terms:
    """Terms from the best analyzer available now (Nori, else the suffix list)."""
    terms = await nori_terms(text)
    if terms is not None:
        return Terms(NORI_ANALYZER, terms)
    return Terms(FALLBACK_ANALYZER, fallback_terms(text))


async def terms_with(text: str, analyzer: str) -> Terms | None:
    """Terms from one named analyzer, to match text stored with it (``None``: unavailable)."""
    if analyzer == FALLBACK_ANALYZER:
        return Terms(FALLBACK_ANALYZER, fallback_terms(text))
    if analyzer == NORI_ANALYZER:
        terms = await nori_terms(text)
        return None if terms is None else Terms(NORI_ANALYZER, terms)
    return None


async def probe() -> tuple[bool, str]:
    """Whether Nori answers (system status): ``(ok, detail)``."""
    if not settings.opensearch_url:
        return False, "not configured; Korean terms use the suffix list"
    started = time.perf_counter()
    terms = await nori_terms("세션을 확인했습니다", timeout_ms=5000)
    if terms is None:
        return False, "no Nori analysis (is analysis-nori installed?)"
    return True, f"nori ({round((time.perf_counter() - started) * 1000)} ms)"


__all__ = [
    "ANALYZERS",
    "FALLBACK_ANALYZER",
    "NORI_ANALYZER",
    "Terms",
    "fallback_terms",
    "korean_terms",
    "nori_terms",
    "probe",
    "terms_with",
]
