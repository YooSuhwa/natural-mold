"""The built-in guardrail templates (guardrail-minimum-plan.md P1: five templates).

Each runs through Moldy's real agent build with a recording fake model, like
``test_guardrail_middleware``. Values are synthetic.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from app.guardrails.rules import BUILTIN_TEMPLATES
from app.guardrails.templates import rules_from_json, rules_to_json
from tests.test_guardrail_middleware import RRN, SECRET, _model, _run, _stored, _text

CARD = "4111-1111-1111-1111"


def test_five_built_in_templates_round_trip_as_data() -> None:
    assert list(BUILTIN_TEMPLATES) == [
        "kr-default",
        "kr-natural",
        "kr-strict",
        "common-basic",
        "kr-observe",
    ]
    for rules in BUILTIN_TEMPLATES.values():
        assert rules_from_json(rules_to_json(rules)["rules"]) == rules.rules


async def test_strict_stops_an_identifier_before_the_model_sees_it() -> None:
    model = _model(AIMessage(content="보내면 안 되는 답"))

    agent, config, _ = await _run(
        model, f"주민번호 {RRN}로 조회", rules=BUILTIN_TEMPLATES["kr-strict"]
    )

    assert model.seen == []
    reply = (await _stored(agent, config))[-1]
    assert "가드레일 정책(kr-identifiers)" in str(reply.content)


async def test_strict_replaces_contacts_instead_of_masking_them() -> None:
    model = _model(AIMessage(content="네."))

    await _run(model, "연락처는 010-1234-5678입니다", rules=BUILTIN_TEMPLATES["kr-strict"])

    sent = _text(model.seen[0])
    assert "010-1234-5678" not in sent and "5678" not in sent


async def test_common_basic_needs_no_korean_pack_and_still_guards_secrets_and_cards() -> None:
    rules = BUILTIN_TEMPLATES["common-basic"]
    assert not {pack for rule in rules.rules for pack in rule.packs} & {"kr", "kr_model"}

    blocked = _model(AIMessage(content="x"))
    await _run(blocked, f"키 {SECRET}", rules=rules)
    assert blocked.seen == []

    model = _model(AIMessage(content="네."))
    await _run(model, f"카드 {CARD}, 주민번호 {RRN}", rules=rules)
    sent = _text(model.seen[0])
    assert CARD not in sent
    assert RRN in sent  # no Korean identifiers in this template


@pytest.mark.parametrize("name", ["kr-strict", "common-basic"])
def test_new_templates_check_answers_too(name: str) -> None:
    stages = {stage for rule in BUILTIN_TEMPLATES[name].rules for stage in rule.stages}
    assert "output" in stages
