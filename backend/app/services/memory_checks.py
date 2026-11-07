"""Checks a memory's text before it is stored (memory-policy-plan.md §5.3-2, Q4).

Pure and synchronous: no DB, no model. The detectors are the guardrail packs
(``app.guardrails.detectors``), run on the text as written (NFKC) and again on the
normalized text, so a number split by spaces or written in Korean digit words is still found.
Secrets stay with ``memory_service._SECRET_PATTERNS``; this module does not look for them.

Order (§5.3-2): normalize, high-risk Korean PII (reject), pseudonym tokens (reject),
sensitive categories, contacts, instruction-like text, other people (flags).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from app.guardrails.detectors import Finding, detect
from app.guardrails.pseudonyms import TOKEN as PSEUDONYM_TOKEN

Sensitivity = Literal["normal", "contact", "sensitive"]

# ko-pii label -> reason code, in the order a rejection is reported.
_REJECT_CODES = {
    "RRN": "MEMORY_PII_RRN",
    "FRN": "MEMORY_PII_FRN",
    "PASSPORT": "MEMORY_PII_PASSPORT",
    "DRIVER_LICENSE": "MEMORY_PII_DRIVER_LICENSE",
    "CARD": "MEMORY_PII_CARD",
    "ACCOUNT": "MEMORY_PII_ACCOUNT",
}
PSEUDONYM_REJECT_CODE = "MEMORY_PSEUDONYM_TOKEN"
_CONTACT_LABELS = frozenset({"PHONE", "EMAIL"})

_KO_DIGITS = {
    "공": "0",
    "영": "0",
    "일": "1",
    "이": "2",
    "삼": "3",
    "사": "4",
    "오": "5",
    "육": "6",
    "칠": "7",
    "팔": "8",
    "구": "9",
}
# Six or more digit syllables (single spaces or hyphens allowed between), so "일이 많다" stays.
_KO_DIGIT_RUN = re.compile(r"[공영일이삼사오육칠팔구](?:[ -]?[공영일이삼사오육칠팔구]){5,}")
# Digit groups split by a space, hyphen or dot. Joined only when the run holds 9+ digits
# (phone, RRN, card, account lengths), so dates and version numbers keep their shape.
_SPLIT_DIGITS = re.compile(r"\d+(?:(?: ?[-.] ?| )\d+)+")
_MIN_JOINED_DIGITS = 9

_KO = r"(?<![가-힣])"  # a Korean keyword starts a word (particles may follow)
_SENSITIVE_PATTERNS = (
    # health / medical
    re.compile(
        _KO + r"(질병|질환|병원|진단|투병|입원|수술|처방|복용|정신과|우울증|공황|불면증|"
        r"당뇨|고혈압|암 진단|항암|치매|장애|임신|난임|유산|건강 상태|병력)"
    ),
    re.compile(
        r"\b(diagnos\w*|illness|disease|depression|anxiety disorder|cancer|diabetes|"
        r"therapist|therapy|medication|prescription|hospitali[sz]ed|pregnan\w*|"
        r"disabilit\w*|hiv|medical condition)\b",
        re.IGNORECASE,
    ),
    # religion
    re.compile(_KO + r"(종교|신앙|교회|성당|사찰|예배|미사|불교|기독교|천주교|개신교|이슬람|무교)"),
    re.compile(
        r"\b(religio\w*|church|mosque|synagogue|christian|muslim|buddhis\w*|catholic|"
        r"jewish|atheist)\b",
        re.IGNORECASE,
    ),
    # trade union
    re.compile(_KO + r"(노조|노동조합|조합원)"),
    re.compile(r"\b(labou?r union|trade union|union member\w*)\b", re.IGNORECASE),
    # politics
    re.compile(
        _KO + r"(정당|정치 성향|정치적 성향|지지 정당|지지하는 정당|당원|더불어민주당|"
        r"국민의힘|정의당|투표했)"
    ),
    re.compile(
        r"\b(political (party|view\w*|affiliation)|voted for|democrat\w*|republican\w*)\b",
        re.IGNORECASE,
    ),
    # sex life / orientation
    re.compile(_KO + r"(성생활|성적 지향|성 정체성|성정체성|동성애|성소수자|양성애|트랜스젠더)"),
    re.compile(
        r"\b(sex life|sexual orientation|gay|lesbian|bisexual|transgender)\b", re.IGNORECASE
    ),
    # criminal record
    re.compile(
        _KO + r"(전과(?![목])|범죄 경력|범죄경력|범죄 기록|징역|집행유예|벌금형|수감|구속|체포|"
        r"실형|기소)"
    ),
    re.compile(
        r"\b(criminal record|convicted|conviction|arrested|felony|prison|jail)\b",
        re.IGNORECASE,
    ),
)

_HONORIFICS = (
    r"(씨|님|팀장|부장|과장|차장|대리|사원|주임|대표|이사|상무|전무|사장|실장|본부장|"
    r"센터장|선생님|교수)"
)
# Common surnames + 1-2 syllables + honorific ("김철수 팀장", "박부장님", "민수씨" needs a surname).
_NAME_HONORIFIC = re.compile(
    _KO + r"[김이박최정강조윤장임한오서신권황안송류홍전고문양손배백허유남심노하곽성차주우구"
    r"민진나지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용]"
    r"[가-힣]{0,2} ?" + _HONORIFICS
)
_RELATIONS = re.compile(
    _KO + r"(어머니|아버지|엄마|아빠|부모님|아내|남편|와이프|배우자|아들|딸(?![기])|"
    r"누나|언니|오빠|여동생|남동생|동생|할머니|할아버지|장모|장인|시어머니|시아버지|"
    r"동료|직장 상사|상사|팀원|친구|여자친구|남자친구|애인)"
)
_RELATIONS_EN = re.compile(
    r"\b(my|his|her|their|our)\s+(mother|father|mom|dad|parents?|wife|husband|spouse|son|"
    r"daughter|brother|sister|colleague|coworker|co-worker|boss|manager|friend|partner|"
    r"girlfriend|boyfriend)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MemoryCheck:
    """``rejected`` is a reason code; a rejected text carries no flags."""

    rejected: str | None
    flags: frozenset[str]
    sensitivity: Sensitivity


def _join_digits(match: re.Match[str]) -> str:
    run = match.group(0)
    digits = re.sub(r"\D", "", run)
    return digits if len(digits) >= _MIN_JOINED_DIGITS else run


def _ko_digits(match: re.Match[str]) -> str:
    return "".join(_KO_DIGITS.get(ch, ch) for ch in match.group(0))


def normalize_for_checks(text: str) -> str:
    """NFKC, Korean digit words in long runs to digits, then split digit runs joined."""
    text = unicodedata.normalize("NFKC", text)
    text = _KO_DIGIT_RUN.sub(_ko_digits, text)
    return _SPLIT_DIGITS.sub(_join_digits, text)


def _findings(text: str) -> list[Finding]:
    raw = unicodedata.normalize("NFKC", text)
    normalized = normalize_for_checks(text)
    found = detect(raw, ("common", "kr"))
    if normalized != raw:
        found += detect(normalized, ("common", "kr"))
    return found


def check_memory_text(text: str) -> MemoryCheck:
    normalized = normalize_for_checks(text)
    findings = _findings(text)
    labels = {f.entity for f in findings}
    for label, code in _REJECT_CODES.items():
        if label in labels:
            return MemoryCheck(rejected=code, flags=frozenset(), sensitivity="sensitive")
    if PSEUDONYM_TOKEN.search(normalized):
        return MemoryCheck(
            rejected=PSEUDONYM_REJECT_CODE, flags=frozenset(), sensitivity="sensitive"
        )

    flags: set[str] = set()
    if any(p.search(normalized) for p in _SENSITIVE_PATTERNS):
        flags.add("sensitive")
    if labels & _CONTACT_LABELS:
        flags.add("contact")
    if detect(normalized, ("jailbreak",)):
        flags.add("instruction_like")
    if (
        "PERSON" in labels
        or _NAME_HONORIFIC.search(normalized)
        or _RELATIONS.search(normalized)
        or _RELATIONS_EN.search(normalized)
    ):
        flags.add("other_person")

    sensitivity: Sensitivity = (
        "sensitive" if "sensitive" in flags else "contact" if "contact" in flags else "normal"
    )
    return MemoryCheck(rejected=None, flags=frozenset(flags), sensitivity=sensitivity)


# --- near-duplicate guard (GBrain, §3.5 / §5.3-2-6) --------------------------------------

_NEGATIONS = (
    re.compile(r"(?<![가-힣])안(?= )"),  # "안 해" (안녕/안내 untouched)
    re.compile(r"않"),
    re.compile(r"(?<![가-힣잘])못"),
    re.compile(r"없"),
    re.compile(r"아니|아닌|아님|아냐"),
    re.compile(r"더 ?이상"),
    re.compile(
        r"\b(not|no longer|never|no|none|don't|doesn't|didn't|isn't|aren't|wasn't|won't|"
        r"can't|cannot|shouldn't)\b",
        re.IGNORECASE,
    ),
)
_UNIT = r"(?: ?(?:만|천|백|억|조))*(?: ?(?:원|달러|엔|위안|유로|%|퍼센트|usd|krw|won|dollars?))?"
_NUMBER = re.compile(r"[$₩€]?\d+(?:[.,]\d+)*" + _UNIT, re.IGNORECASE)
_KO_AMOUNT = re.compile(r"(?<![가-힣])[일이삼사오육칠팔구십백천만억조]+ ?(?:원|달러)")
_DATE_WORDS = re.compile(
    r"(월|화|수|목|금|토|일)요일|오늘|내일|모레|어제|그제|그저께|"
    r"(이번|다음|지난|저번) ?(주|달|해)|지난주|다음주|이번주|지난달|다음달|이번달|"
    r"작년|올해|내년|재작년|주말|평일|아침|점심|저녁|오전|오후|"
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun|"
    r"january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec|today|tomorrow|yesterday|tonight|"
    r"weekend|weekday|morning|afternoon|evening)\b",
    re.IGNORECASE,
)


def _claim_markers(text: str) -> Counter[str]:
    text = normalize_for_checks(text).casefold()
    markers: Counter[str] = Counter()
    for i, pattern in enumerate(_NEGATIONS):
        markers[f"neg{i}"] += len(pattern.findall(text))
    for m in _NUMBER.finditer(text):
        markers["num:" + re.sub(r"[ ,]", "", m.group(0))] += 1
    for m in _KO_AMOUNT.finditer(text):
        markers["amt:" + m.group(0).replace(" ", "")] += 1
    for m in _DATE_WORDS.finditer(text):
        markers["date:" + m.group(0).replace(" ", "")] += 1
    return +markers  # drop zero counts


def claims_diverge(a: str, b: str) -> bool:
    """True when negation, numbers, amounts or dates differ: never merge these two texts."""
    return _claim_markers(a) != _claim_markers(b)


_EDGE_PUNCT = re.compile(r"^[\W_]+|[\W_]+$")


def content_fingerprint(text: str) -> str:
    """Normalized text for exact-duplicate comparison (casefold, spaces, edge punctuation)."""
    text = " ".join(normalize_for_checks(text).casefold().split())
    return _EDGE_PUNCT.sub("", text)
