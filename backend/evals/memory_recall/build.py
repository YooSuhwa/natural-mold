"""Build the synthetic recall set: memories (gpt-5.4-mini), questions (claude-sonnet-5), vectors.

    cd backend && .venv/bin/python -m evals.memory_recall.build

Every LLM reply and vector is cached under ``cache/``, so a re-run makes no calls. The people
are imagined; the prompts forbid contacts, ID numbers and other real personal data.
"""

# ruff: noqa: E501 - the prompts are cache keys; rewrapping them would re-run the LLM calls
from __future__ import annotations

import json
import random
import re
import statistics
from pathlib import Path

from evals.memory_recall import gateway

HERE = Path(__file__).parent
DATASET = HERE / "dataset.json"
LATENCY = HERE / "cache" / "embedding_latency.json"
SEED = 15
TODAY = "2026-11-07"
MEMORY_MODEL = "gpt-5.4-mini"
QUESTION_MODEL = "claude-sonnet-5"
EMBEDDING_MODELS = ("text-embedding-3-large", "text-embedding-3-small")
KINDS = ("fact", "preference", "commitment", "experience", "procedure")
CATEGORIES = ("same_word", "particle", "paraphrase", "time", "multi_turn", "unrelated")
PER_PERSON = {
    "same_word": 3,
    "particle": 3,
    "paraphrase": 3,
    "time": 2,
    "multi_turn": 2,
    "unrelated": 3,
}

# (id, who, what their agent does) — all imagined.
PEOPLE = [
    ("p01", "백엔드 개발자 '도윤'", "코드 리뷰와 배포를 돕는 개발 비서"),
    ("p02", "브랜드 마케터 '서연'", "캠페인 기획과 보고서를 돕는 마케팅 비서"),
    ("p03", "고등학교 국어 교사 '지호'", "수업 준비와 학부모 상담을 돕는 교무 비서"),
    ("p04", "핀테크 스타트업 PM '하린'", "로드맵과 회의록을 관리하는 제품 비서"),
    ("p05", "중견기업 재무 담당 '민준'", "결산과 예산 검토를 돕는 재무 비서"),
    ("p06", "생물학 연구원 '수아'", "실험 기록과 논문 작업을 돕는 연구 비서"),
    ("p07", "UX 디자이너 '예준'", "디자인 리뷰와 사용성 테스트를 돕는 디자인 비서"),
    ("p08", "B2B 영업 관리자 '지안'", "고객사 미팅과 제안서를 돕는 영업 비서"),
    ("p09", "동네 카페 운영자 '은우'", "재고, 직원 근무표, 메뉴 개발을 돕는 매장 비서"),
    ("p10", "데이터과학 대학원생 '유나'", "논문 읽기와 실험 코드를 돕는 연구실 비서"),
]

MEMORY_PROMPT = """너는 AI 비서의 장기 기억 저장소를 채우는 합성 데이터를 만든다.
사람: {who}. 이 사람의 에이전트: {agent}. 오늘은 {today}이다.

이 사람에 대해 비서가 기억해 둘 만한 한국어 기억 30개를 만들어라.
- 종류(kind)별 개수: fact 7, preference 7, commitment 5, experience 7, procedure 4.
- experience는 실제로 있었던 일 하나씩이다. occurred_start(YYYY-MM-DD)를 2026-04-01 ~ 2026-11-06 사이로
  넣고, 여러 날에 걸친 일이면 occurred_end도 넣는다. 다른 종류는 둘 다 null.
- commitment는 앞으로 하기로 한 일(마감, 약속)이다.
- scope: 이 사람 자신에 관한 것은 "user", 이 에이전트의 업무 방식·작업 규칙은 "agent". agent는 9~11개.
- importance: 0~3 정수(3이 가장 중요).
- 각 기억은 한두 문장, 25~90자. 구체적인 고유명사·숫자·도구 이름을 섞는다.
- 같은 주제 낱말(예: 회의, 보고서, 고객)이 여러 기억에 나오게 해서 검색이 쉽지 않게 한다.
- 전화번호, 이메일, 주소, 주민등록번호, 계좌번호, 건강·종교·정치 정보, 실존 인물 이름은 넣지 않는다.

JSON 객체 하나만 출력한다:
{{"memories": [{{"content": "...", "kind": "fact", "scope": "user", "importance": 1,
"occurred_start": null, "occurred_end": null}}, ...]}}"""

QUESTION_PROMPT = """너는 AI 비서 기억 검색 평가 세트의 질문을 쓴다. 기억을 쓴 사람과 다른 사람이다.
사람: {who}. 오늘은 {today}이다. 아래는 이 사람의 저장된 기억 목록이다(id, 종류, 날짜, 내용).

{memories}

이 사람이 비서와 채팅하며 실제로 칠 법한 한국어 질문을 범주별로 정확히 다음 개수만큼 써라:
- same_word {same_word}개: 대상 기억의 특징적인 낱말을 형태 그대로(같은 조사 또는 조사 없이) 쓴다.
- particle {particle}개: 대상 기억의 핵심 명사를 쓰되, 기억에 없는 조사를 붙인다(기억이 "세션 설정"이면
  질문은 "세션을", "세션에서는"처럼). 그 밖의 낱말은 되도록 겹치지 않게 한다.
- paraphrase {paraphrase}개: 대상 기억과 내용어(명사·동사 어간)를 하나도 공유하지 않고 다른 말로 묻는다.
- time {time}개: 오늘 기준 상대 시간 표현("지난달", "두 달 전", "지난 여름")으로 experience 기억을 묻는다.
- multi_turn {multi_turn}개: "previous"(직전 사용자 메시지)가 주제를 꺼내고, "question"은 "그거", "거기서",
  "그때"처럼 앞 메시지 없이는 무엇을 묻는지 모르는 말로 묻는다. question에는 주제 낱말을 넣지 않는다.
- unrelated {unrelated}개: 이 사람이 물을 법하지만 어떤 기억도 답에 도움이 되지 않는 질문.

규칙: gold는 그 질문에 답하려면 필요한 기억 id 목록(1~3개, 보통 1개)이고 unrelated는 빈 목록이다.
같은 기억을 여러 질문의 대상으로 써도 된다. multi_turn이 아니면 previous는 null.

JSON 객체 하나만 출력한다:
{{"questions": [{{"category": "same_word", "question": "...", "previous": null, "gold": ["{pid}-m01"]}}, ...]}}"""


def _json(reply: str) -> dict:
    match = re.search(r"\{.*\}", reply, re.DOTALL)
    if match is None:
        raise ValueError("no JSON object in the reply")
    return json.loads(match.group(0))


def _check(ok: bool, row: dict) -> None:
    if not ok:
        raise ValueError(f"unexpected LLM row: {row}")


def _memories(pid: str, who: str, agent: str) -> list[dict]:
    reply = gateway.chat(MEMORY_MODEL, MEMORY_PROMPT.format(who=who, agent=agent, today=TODAY))
    rows = _json(reply)["memories"]
    out = []
    for i, row in enumerate(rows, start=1):
        _check(row["kind"] in KINDS and row["scope"] in {"user", "agent"}, row)
        out.append(
            {
                "id": f"{pid}-m{i:02d}",
                "person": pid,
                "content": row["content"].strip(),
                "kind": row["kind"],
                "scope": row["scope"],
                "importance": int(row.get("importance") or 1),
                "occurred_start": row.get("occurred_start"),
                "occurred_end": row.get("occurred_end"),
            }
        )
    return out


def _questions(pid: str, who: str, memories: list[dict]) -> list[dict]:
    listing = "\n".join(
        f"{m['id']} | {m['kind']} | {m['occurred_start'] or '-'} | {m['content']}" for m in memories
    )
    prompt = QUESTION_PROMPT.format(who=who, today=TODAY, memories=listing, pid=pid, **PER_PERSON)
    rows = _json(gateway.chat(QUESTION_MODEL, prompt))["questions"]
    known = {m["id"] for m in memories}
    out = []
    for row in rows:
        category, gold = row["category"], list(row.get("gold") or [])
        _check(category in CATEGORIES and set(gold) <= known, row)
        _check((category == "unrelated") == (not gold), row)
        previous = row.get("previous") if category == "multi_turn" else None
        _check(category != "multi_turn" or bool(previous), row)
        out.append(
            {
                "id": f"{pid}-q{len(out) + 1:02d}",
                "person": pid,
                "category": category,
                "question": row["question"].strip(),
                "previous": previous,
                "gold": gold,
            }
        )
    return out


def _groups(questions: list[dict]) -> dict[str, str]:
    """Questions that share a gold memory form one group (union-find); unrelated stand alone."""
    parent = {q["id"]: q["id"] for q in questions}

    def find(x: str) -> str:
        while parent[x] != x:
            x = parent[x]
        return x

    owner: dict[str, str] = {}
    for q in questions:
        for memory_id in q["gold"]:
            if memory_id in owner:
                parent[find(q["id"])] = find(owner[memory_id])
            else:
                owner[memory_id] = q["id"]
    return {q["id"]: f"g-{find(q['id'])}" for q in questions}


def _split(questions: list[dict]) -> None:
    """Whole groups go to ``tune`` or ``report``, balancing the category counts."""
    by_group: dict[str, list[dict]] = {}
    for q in questions:
        by_group.setdefault(q["group"], []).append(q)
    groups = sorted(by_group)
    random.Random(SEED).shuffle(groups)  # noqa: S311 - a reproducible split, not a secret
    counts = {half: dict.fromkeys(CATEGORIES, 0) for half in ("tune", "report")}
    for group in groups:
        members = by_group[group]
        half = min(counts, key=lambda h: (sum(counts[h][q["category"]] for q in members), h))
        for q in members:
            q["split"] = half
            counts[half][q["category"]] += 1


def query_text(question: dict) -> str:
    """What ``rank`` embeds: the latest message alone, or latest + earlier joined by newline."""
    return "\n".join(m for m in (question["question"], question["previous"]) if m)


def main() -> None:
    memories: list[dict] = []
    questions: list[dict] = []
    for pid, who, agent in PEOPLE:
        mine = _memories(pid, who, agent)
        memories += mine
        questions += _questions(pid, who, mine)
        print(f"{pid}: {len(mine)} memories, {len(questions)} questions so far", flush=True)
    groups = _groups(questions)
    for q in questions:
        q["group"] = groups[q["id"]]
    _split(questions)
    texts = [m["content"] for m in memories] + [query_text(q) for q in questions]
    for model in EMBEDDING_MODELS:
        gateway.Vectors(model).ensure(texts)
    if not LATENCY.exists():
        vectors = gateway.Vectors(EMBEDDING_MODELS[0])
        samples = [vectors.timed_single(query_text(q)) for q in questions[:20]]
        LATENCY.write_text(
            json.dumps(
                {
                    "model": f"{EMBEDDING_MODELS[0]}@{gateway.DIMENSIONS}",
                    "samples_ms": [round(s, 1) for s in samples],
                    "p50_ms": round(statistics.median(samples), 1),
                    "p95_ms": round(sorted(samples)[int(0.95 * len(samples)) - 1], 1),
                },
                indent=1,
            )
        )
    dataset = {
        "seed": SEED,
        "today": TODAY,
        "memory_model": MEMORY_MODEL,
        "question_model": QUESTION_MODEL,
        "people": [{"id": p, "who": w, "agent": a} for p, w, a in PEOPLE],
        "memories": memories,
        "questions": questions,
    }
    DATASET.write_text(json.dumps(dataset, ensure_ascii=False, indent=1))
    print(f"{len(memories)} memories, {len(questions)} questions; calls {gateway.calls}")


if __name__ == "__main__":
    main()
