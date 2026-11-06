"""Apply guardrail actions to text spans (guardrail-minimum-plan.md §3.3).

Pure functions: findings + a rule set in, the changed text and the decisions out. Overlapping
findings become one span carrying the strongest action, so no character a rule covered is
left in clear.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass

from app.guardrails.detectors import Finding
from app.guardrails.rules import ACTION_STRENGTH, Action, Rule, RuleSet, Stage

# What a replaced value reads as; unknown entities fall back to the generic label.
LABELS: dict[str, str] = {
    "PERSON": "이름",
    "ADDRESS": "주소",
    "PHONE": "전화번호",
    "EMAIL": "이메일",
    "RRN": "주민등록번호",
    "FRN": "외국인등록번호",
    "PASSPORT": "여권번호",
    "DRIVER_LICENSE": "운전면허번호",
    "CARD": "카드번호",
    "ACCOUNT": "계좌번호",
    "MEDICAL_INSURANCE": "건강보험번호",
    "IP": "IP 주소",
    "URL": "URL",
    "SECRET": "비밀값",
    "JAILBREAK": "지시 무시·탈옥 시도",
}
_GENERIC_LABEL = "개인정보"


@dataclass(frozen=True, slots=True)
class Decision:
    rule: str
    entity: str
    action: Action
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Applied:
    text: str
    decisions: tuple[Decision, ...]

    @property
    def blocked(self) -> bool:
        return any(d.action == "block" for d in self.decisions)


def label(entity: str) -> str:
    return LABELS.get(entity, _GENERIC_LABEL)


def _mask(value: str, entity: str) -> str:
    if entity == "EMAIL" and "@" in value:
        local, _, domain = value.partition("@")
        return f"{local[:1]}{'*' * max(len(local) - 1, 1)}@{domain}"
    digits = [i for i, ch in enumerate(value) if ch.isdigit()]
    if len(digits) > 4:  # keep the last four digits, keep separators
        hidden = set(digits[:-4])
        return "".join("*" if i in hidden else ch for i, ch in enumerate(value))
    return value[:1] + "*" * max(len(value) - 1, 1)


def _hash(value: str, entity: str, key: bytes) -> str:
    digest = hmac.new(key, f"{entity}\x00{value}".encode(), hashlib.sha256).hexdigest()
    return f"[{label(entity)}:{digest[:10]}]"


def _replacement(value: str, entity: str, action: Action, hash_key: bytes) -> str:
    if action == "mask":
        return _mask(value, entity)
    if action == "hash":
        return _hash(value, entity, hash_key)
    return f"[{label(entity)}]"


def _spans(
    findings: list[Finding], rules: RuleSet, stage: Stage
) -> list[tuple[int, int, str, Rule]]:
    """Matching findings merged into non-overlapping spans with their strongest rule."""
    matched = sorted(
        (f.start, f.end, f.entity, rule)
        for f in findings
        if (rule := rules.rule_for(f.entity, f.pack, stage)) is not None
    )
    merged: list[tuple[int, int, str, Rule]] = []
    for start, end, entity, rule in matched:
        if merged and start < merged[-1][1]:
            p_start, p_end, p_entity, p_rule = merged[-1]
            stronger = ACTION_STRENGTH[rule.action] > ACTION_STRENGTH[p_rule.action]
            merged[-1] = (
                p_start,
                max(p_end, end),
                entity if stronger else p_entity,
                rule if stronger else p_rule,
            )
        else:
            merged.append((start, end, entity, rule))
    return merged


def apply(
    text: str,
    findings: list[Finding],
    rules: RuleSet,
    stage: Stage,
    hash_key: bytes,
    pseudonym: Callable[[str, str, Rule], str] | None = None,
) -> Applied:
    """``pseudonym(entity, value, rule)`` gives the conversation's pseudonym; without it a
    ``pseudonymize`` rule redacts instead (nothing could restore the value)."""
    pieces: list[str] = []
    decisions: list[Decision] = []
    cursor = 0
    for start, end, entity, rule in _spans(findings, rules, stage):
        decisions.append(Decision(rule.id, entity, rule.action, start, end))
        pieces.append(text[cursor:start])
        value = text[start:end]
        if rule.action in ("warn", "block", "approval"):
            pieces.append(value)
        elif rule.action == "pseudonymize":
            pieces.append(pseudonym(entity, value, rule) if pseudonym else f"[{label(entity)}]")
        else:
            pieces.append(_replacement(value, entity, rule.action, hash_key))
        cursor = end
    pieces.append(text[cursor:])
    return Applied("".join(pieces), tuple(decisions))


def block_notice(decisions: tuple[Decision, ...]) -> str:
    rules = ", ".join(sorted({d.rule for d in decisions if d.action == "block"}))
    entities = ", ".join(sorted({label(d.entity) for d in decisions if d.action == "block"}))
    return (
        f"가드레일 정책({rules})에 따라 요청을 보내지 않았습니다. "
        f"{entities}이(가) 포함되어 있습니다. 해당 내용을 빼고 다시 시도해 주세요."
    )


def approval_refusal(decisions: tuple[Decision, ...]) -> str:
    rules = ", ".join(sorted({d.rule for d in decisions if d.action == "approval"}))
    entities = ", ".join(sorted({label(d.entity) for d in decisions if d.action == "approval"}))
    return (
        f"가드레일 정책({rules})에 따라 사용자 승인이 필요하지만 승인할 수 없는 실행이라 "
        f"도구를 실행하지 않았습니다. 인자에 {entities}이(가) 포함되어 있습니다."
    )
