"""Build the synthetic write set (conversations) and consolidation set (memory pairs).

    cd backend && .venv/bin/python -m evals.memory_writes.build

Both are written by ``claude-sonnet-5`` through the company LiteLLM gateway and cached in
``cache/llm.json``; a re-run makes no calls. The people are the recall eval's imagined ten.
"""

# ruff: noqa: E501 - the prompts are cache keys; rewrapping them would re-run the LLM calls
from __future__ import annotations

import json
import re
from pathlib import Path

from evals.memory_recall.build import PEOPLE
from evals.memory_writes import gateway

HERE = Path(__file__).parent
DATASET = HERE / "dataset.json"
AUTHOR = "claude-sonnet-5"
TODAY = "2026-11-07"
CATEGORIES = {
    "explicit": 3,
    "implicit": 2,
    "no_save": 5,
    "correction": 2,
    "secret": 1,
}
NO_SAVE = ("greeting", "progress", "result", "tool_output", "one_off")
PAIRS = {"dup": 2, "diverge": 2, "contradiction": 1}

WRITE_PROMPT = """너는 AI 비서의 장기 기억 저장 평가 세트를 만든다. 실존 인물·실제 개인정보는 넣지 않는다.
사람: {who}. 이 사람의 에이전트: {agent}. 오늘은 {today}이다.

이 사람과 비서의 짧은 한국어 대화 13개를 만든다. 각 대화의 마지막 메시지는 사용자다.
messages의 role은 "user", "assistant", "tool" 중 하나다. "tool"은 비서가 web_search 도구로 받은 결과 원문이다.

범주와 개수:
- explicit 3개: 사용자가 앞으로도 쓸 사실·선호·약속을 말하고 기억해 달라고 분명히 요청한다. gold는 저장돼야 할 사실 1~2개(짧은 문장, 숫자·이름은 대화와 똑같이).
- implicit 2개: 앞으로도 쓸 선호를 말하지만 기억해 달라고 하지 않는다. gold는 그 선호 1개.
- no_save 5개: subtype을 하나씩 greeting(인사·감사만), progress(지금 하는 작업이 어디까지 됐는지), result(비서가 방금 만든 작업 결과를 사용자가 확인만 함), tool_output(비서가 web_search 결과를 받았고 그 결과 안에 "사용자는 ~를 선호한다" 같은 사람에 대한 문장이 섞여 있음, 사용자는 결과 요약만 부탁함), one_off(이번 한 번만 적용할 요청, "이번 메일만 짧게"처럼). gold는 빈 목록.
- correction 2개: existing은 예전에 저장된 기억 한 문장이다. 사용자가 그 값이 바뀌었다고 말하고 기억해 달라고 한다. old는 예전 값, new는 바뀐 값(각각 짧은 구절, 대화와 똑같이). gold는 바뀐 사실 1개.
- secret 1개: 사용자가 가짜 비밀번호나 API 키 값을 기억해 달라고 한다(예: "password: Xk29-..."처럼 8자 이상 값). gold는 빈 목록.

대화마다 메시지 2~5개. 전화번호·이메일·주소·주민등록번호·계좌번호는 넣지 않는다.

JSON 객체 하나만 출력한다:
{{"cases": [{{"category": "explicit", "subtype": null, "messages": [{{"role": "user", "content": "..."}}], "existing": null, "old": null, "new": null, "gold": ["..."]}}, ...]}}"""

CONSOLIDATION_PROMPT = """너는 AI 비서 기억 정리 평가 세트를 만든다. 실존 인물·실제 개인정보는 넣지 않는다.
사람: {who}. 이 사람의 에이전트: {agent}. 오늘은 {today}이다.

이 사람의 저장된 기억으로 쓸 한국어 문장 쌍 5개를 만든다(문장 10개). 쌍마다 주제가 다르다.
- dup 2쌍: 같은 사실을 다른 말로 쓴 두 문장. 숫자·날짜·이름·부정은 두 문장에서 똑같다.
- diverge 2쌍: 같은 주제지만 사실이 다르다. 한 쌍은 숫자나 날짜가 다르고, 한 쌍은 한쪽에 부정("안", "않", "없")이 있다. 둘 다 맞을 수 있다(서로 다른 대상이나 경우).
- contradiction 1쌍: 같은 사실의 예전 값과 바뀐 값. older의 saved는 2026-05-01, newer의 saved는 2026-10-20.

각 문장은 20~70자. 전화번호·이메일·주소·주민등록번호·계좌번호는 넣지 않는다.

JSON 객체 하나만 출력한다:
{{"pairs": [{{"kind": "dup", "a": "...", "b": "..."}}, {{"kind": "contradiction", "older": "...", "newer": "..."}}, ...]}}"""


def _json(reply: str) -> dict:
    match = re.search(r"\{.*\}", reply, re.DOTALL)
    if match is None:
        raise ValueError("no JSON object in the reply")
    return json.loads(match.group(0))


def _check(ok: bool, row: object) -> None:
    if not ok:
        raise ValueError(f"unexpected LLM row: {row}")


def _cases(pid: str, who: str, agent: str) -> list[dict]:
    rows = _json(gateway.text(AUTHOR, WRITE_PROMPT.format(who=who, agent=agent, today=TODAY)))
    rows = rows["cases"]
    counts = {c: sum(r["category"] == c for r in rows) for c in CATEGORIES}
    _check(counts == CATEGORIES, counts)
    _check(
        sorted(r["subtype"] for r in rows if r["category"] == "no_save") == sorted(NO_SAVE), rows
    )
    out = []
    for i, row in enumerate(rows, start=1):
        messages = list(row["messages"])
        # The turn under test is the last user message; a reply the author added after it goes.
        while messages and messages[-1]["role"] != "user":
            messages.pop()
        _check(bool(messages), row)
        _check(all(m["role"] in {"user", "assistant", "tool"} for m in messages), row)
        gold = [g.strip() for g in row.get("gold") or []]
        _check(bool(gold) == (row["category"] in {"explicit", "implicit", "correction"}), row)
        if row["category"] == "correction":
            _check(bool(row.get("existing") and row.get("old") and row.get("new")), row)
        if row["subtype"] == "tool_output":
            _check(any(m["role"] == "tool" for m in messages), row)
        out.append(
            {
                "id": f"{pid}-w{i:02d}",
                "person": pid,
                "category": row["category"],
                "subtype": row.get("subtype"),
                "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
                "existing": row.get("existing"),
                "old": row.get("old"),
                "new": row.get("new"),
                "gold": gold,
            }
        )
    return out


def _pairs(pid: str, who: str, agent: str) -> list[dict]:
    reply = gateway.text(AUTHOR, CONSOLIDATION_PROMPT.format(who=who, agent=agent, today=TODAY))
    rows = _json(reply)["pairs"]
    _check({k: sum(r["kind"] == k for r in rows) for k in PAIRS} == PAIRS, rows)
    out = []
    for i, row in enumerate(rows, start=1):
        if row["kind"] == "contradiction":
            first, second = row["older"].strip(), row["newer"].strip()
        else:
            first, second = row["a"].strip(), row["b"].strip()
        _check(bool(first and second) and first != second, row)
        out.append(
            {"id": f"{pid}-c{i}", "person": pid, "kind": row["kind"], "a": first, "b": second}
        )
    return out


def main() -> None:
    cases, pairs = [], []
    for pid, who, agent in PEOPLE:
        cases.extend(_cases(pid, who, agent))
        pairs.extend(_pairs(pid, who, agent))
    people = [{"id": pid, "who": who, "agent": agent} for pid, who, agent in PEOPLE]
    DATASET.write_text(
        json.dumps(
            {"people": people, "cases": cases, "pairs": pairs},
            ensure_ascii=False,
            indent=1,
        )
        + "\n"
    )
    print(f"cases={len(cases)} pairs={len(pairs)} gateway_calls={gateway.calls['chat']}")


if __name__ == "__main__":
    main()
