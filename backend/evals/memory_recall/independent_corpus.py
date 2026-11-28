"""Validate the independent Korean report corpus before any measurements or provider calls."""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

from evals.memory_recall.build import CATEGORIES

MINIMUM_PER_CATEGORY = 25
Identifier = Annotated[str, Field(pattern=r"^\S+$")]


def _iso_day(value: str) -> str:
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError(f"expected a YYYY-MM-DD date, got {value!r}")
    return value


IsoDay = Annotated[str, AfterValidator(_iso_day)]


class _Person(BaseModel):
    id: Identifier


class _Memory(BaseModel):
    id: Identifier
    person: Identifier
    content: str
    kind: Literal["fact", "preference", "commitment", "experience", "episode", "procedure"]
    scope: Literal["user", "agent"]
    importance: Annotated[int, Field(strict=True, ge=0, le=3)]
    occurred_start: IsoDay | None
    occurred_end: IsoDay | None


class _Question(BaseModel):
    id: Identifier
    person: Identifier
    question: str
    previous: str | None
    gold: list[Identifier]
    group: Identifier
    category: str
    split: Literal["report", "tune"]


class _Corpus(BaseModel):
    today: IsoDay
    people: list[_Person]
    memories: list[_Memory]
    questions: list[_Question]


def _text_key(value: str) -> str:
    """NFKC composes Hangul and folds full-width characters; ignore spacing and punctuation."""
    key = "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())
    if not key:
        raise ValueError("corpus text needs letters or numbers")
    return key


def _unique(values: Iterable[str], name: str) -> set[str]:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate {name}: {value}")
        seen.add(value)
    return seen


def _ids(corpus: _Corpus) -> Iterable[str]:
    return (row.id for rows in (corpus.people, corpus.memories, corpus.questions) for row in rows)


def _question_turns(question: _Question) -> tuple[str, ...]:
    if question.previous is None:
        return (_text_key(question.question),)
    return _text_key(question.question), _text_key(question.previous)


def validate_corpus(data: dict, historical: dict) -> None:
    """Reject overlap with either original split, malformed labels and undersized categories."""
    report = _Corpus.model_validate(data)
    archive = _Corpus.model_validate(historical)
    if _unique(_ids(report), "report ID").intersection(_ids(archive)):
        raise ValueError("an ID overlaps the historical corpus")
    people = {person.id for person in report.people}
    memories = {memory.id: memory for memory in report.memories}
    memory_texts = _unique((_text_key(m.content) for m in report.memories), "report memory")
    if memory_texts.intersection(_text_key(m.content) for m in archive.memories):
        raise ValueError("memory content overlaps the historical corpus")

    for memory in report.memories:
        if memory.person not in people:
            raise ValueError(f"{memory.id}: unknown memory owner")
        start, end = memory.occurred_start, memory.occurred_end
        if end is not None and (start is None or end < start):
            raise ValueError(f"{memory.id}: invalid occurrence date range")
        if memory.kind in {"experience", "episode"} and (end or start or "") > report.today:
            raise ValueError(f"{memory.id}: experience occurs after the evaluation date")

    historical_turns = {turn for q in archive.questions for turn in _question_turns(q)}
    report_queries: set[tuple[str, ...]] = set()
    groups: dict[str, set[str]] = defaultdict(set)
    gold_groups: dict[str, str] = {}
    for q in report.questions:
        if q.person not in people or q.category not in CATEGORIES or q.split != "report":
            raise ValueError(f"{q.id}: invalid owner, category or split for a report query")
        if (q.category == "multi_turn") != (q.previous is not None):
            raise ValueError(f"{q.id}: only multi_turn questions must have a previous turn")
        turns = _question_turns(q)
        if historical_turns.intersection(turns):
            raise ValueError(f"{q.id}: a query turn overlaps the historical corpus")
        if turns in report_queries:
            raise ValueError(f"{q.id}: duplicate report query")
        report_queries.add(turns)
        if (q.category == "unrelated") != (not q.gold):
            raise ValueError(f"{q.id}: only unrelated questions must have an empty gold list")
        for memory_id in _unique(q.gold, f"gold ID for {q.id}"):
            if memory_id not in memories or memories[memory_id].person != q.person:
                raise ValueError(f"{q.id}: missing gold memory or wrong gold owner")
            if gold_groups.setdefault(memory_id, q.group) != q.group:
                raise ValueError(f"{q.id}: shared gold must share a bootstrap group")
        groups[q.category].add(q.group)

    for category in CATEGORIES:
        if len(groups[category]) < MINIMUM_PER_CATEGORY:
            raise ValueError(
                f"{category}: need at least {MINIMUM_PER_CATEGORY} independent question groups"
            )
