from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from typing import Final

from app.schemas.skill_builder import JsonValue, SkillDraftFile, SkillDraftPackage

SEED: Final = 42
RUNS_PER_QUERY: Final = 3
MAX_DESCRIPTION_LENGTH: Final = 1024
WORD_RE: Final = re.compile(r"[\w가-힣]{3,}")

type JsonObject = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class TriggerExample:
    query: str
    should_trigger: bool


@dataclass(frozen=True, slots=True)
class TriggerSplit:
    train: list[TriggerExample]
    test: list[TriggerExample]


def optimize_trigger_description(
    *,
    draft: SkillDraftPackage,
    intent: str,
) -> tuple[SkillDraftPackage, JsonObject]:
    examples = generate_trigger_examples(
        name=draft.name,
        description=draft.description,
        intent=intent,
    )
    split = deterministic_split(examples, seed=SEED)
    rewritten = rewrite_description(
        name=draft.name,
        description=draft.description,
        intent=intent,
    )
    candidates = [
        _candidate_result("before", draft.description, draft=draft, split=split),
        _candidate_result("after", rewritten, draft=draft, split=split),
    ]
    best = select_best_candidate(candidates)
    updated = _draft_with_description(draft, str(best["description"]))
    return updated, {
        "seed": SEED,
        "runs_per_query": RUNS_PER_QUERY,
        "query_sets": {
            "train": [_example_json(example) for example in split.train],
            "test": [_example_json(example) for example in split.test],
        },
        "candidates": candidates,
        "selected": best,
    }


def generate_trigger_examples(*, name: str, description: str, intent: str) -> list[TriggerExample]:
    positive = [
        f"{name} 도와줘",
        intent,
        description,
        f"{_keyword_phrase(description)} 작업을 처리해줘",
    ]
    negative = [
        "오늘 날씨와 교통 상황을 알려줘",
        "이미지 배경을 파란색으로 바꿔줘",
        "데이터베이스 마이그레이션 오류를 디버깅해줘",
        "캘린더 일정을 새로 예약해줘",
    ]
    return [TriggerExample(query=item, should_trigger=True) for item in positive] + [
        TriggerExample(query=item, should_trigger=False) for item in negative
    ]


def deterministic_split(examples: list[TriggerExample], *, seed: int = SEED) -> TriggerSplit:
    positives = [example for example in examples if example.should_trigger]
    negatives = [example for example in examples if not example.should_trigger]
    rng = random.Random(seed)
    rng.shuffle(positives)
    rng.shuffle(negatives)
    test = [positives[0], negatives[0]]
    train = positives[1:] + negatives[1:]
    rng.shuffle(train)
    return TriggerSplit(train=train, test=test)


def classify_trigger(*, name: str, description: str, query: str) -> bool:
    query_text = query.casefold()
    name_hit = name.casefold() in query_text
    tokens = _tokens(description)
    overlap = sum(1 for token in tokens if token in query_text)
    return name_hit or overlap >= 2


def select_best_candidate(candidates: list[JsonObject]) -> JsonObject:
    return max(
        candidates,
        key=lambda item: (float(item["test_score"]), float(item["train_score"])),
    )


def rewrite_description(*, name: str, description: str, intent: str) -> str:
    keywords = ", ".join(_tokens(f"{intent} {description}")[:8])
    rewritten = (
        f"Use when the user asks for {intent.strip()}. "
        f"Trigger for {name.strip()} tasks involving {keywords}. "
        f"Do not use for unrelated scheduling, image editing, or debugging requests."
    )
    return rewritten[:MAX_DESCRIPTION_LENGTH]


def _candidate_result(
    label: str,
    description: str,
    *,
    draft: SkillDraftPackage,
    split: TriggerSplit,
) -> JsonObject:
    train = _score_examples(name=draft.name, description=description, examples=split.train)
    test = _score_examples(name=draft.name, description=description, examples=split.test)
    return {
        "label": label,
        "description": description[:MAX_DESCRIPTION_LENGTH],
        "train_score": train["score"],
        "test_score": test["score"],
        "train_results": train["results"],
        "test_results": test["results"],
    }


def _score_examples(
    *,
    name: str,
    description: str,
    examples: list[TriggerExample],
) -> JsonObject:
    results = [
        _run_query(name=name, description=description, example=example) for example in examples
    ]
    correct = sum(1 for result in results if result["correct"] is True)
    score = correct / len(results) if results else 0
    return {"score": round(score, 6), "results": results}


def _run_query(*, name: str, description: str, example: TriggerExample) -> JsonObject:
    runs = [
        classify_trigger(name=name, description=description, query=example.query)
        for _index in range(RUNS_PER_QUERY)
    ]
    trigger_rate = sum(1 for run in runs if run) / RUNS_PER_QUERY
    predicted = trigger_rate >= 0.5
    return {
        "query": example.query,
        "should_trigger": example.should_trigger,
        "runs": runs,
        "trigger_rate": round(trigger_rate, 6),
        "correct": predicted == example.should_trigger,
    }


def _draft_with_description(draft: SkillDraftPackage, description: str) -> SkillDraftPackage:
    files = [
        _file_with_description(file, description) if file.path == "SKILL.md" else file
        for file in draft.files
    ]
    return draft.model_copy(update={"description": description, "files": files})


def _file_with_description(file: SkillDraftFile, description: str) -> SkillDraftFile:
    return file.model_copy(
        update={"content": _replace_frontmatter_description(file.content, description)}
    )


def _replace_frontmatter_description(content: str, description: str) -> str:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return content
    end_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        -1,
    )
    if end_index < 0:
        return content
    rendered = f"description: {json.dumps(description, ensure_ascii=False)}"
    for index in range(1, end_index):
        if lines[index].startswith("description:"):
            lines[index] = rendered
            return "\n".join(lines) + ("\n" if content.endswith("\n") else "")
    lines.insert(end_index, rendered)
    return "\n".join(lines) + ("\n" if content.endswith("\n") else "")


def _keyword_phrase(text: str) -> str:
    tokens = _tokens(text)
    return " ".join(tokens[:3]) if tokens else "관련"


def _tokens(text: str) -> list[str]:
    seen: set[str] = set()
    tokens: list[str] = []
    for match in WORD_RE.finditer(text.casefold()):
        token = match.group(0)
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _example_json(example: TriggerExample) -> JsonObject:
    return {"query": example.query, "should_trigger": example.should_trigger}
