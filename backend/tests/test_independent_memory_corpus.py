from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from evals.memory_recall.build import CATEGORIES
from evals.memory_recall.independent_corpus import validate_corpus


def _corpus(prefix: str, per_category: int) -> dict:
    owner = f"{prefix}-person"
    memories, questions = [], []
    for category in CATEGORIES:
        for i in range(per_category):
            key = f"{prefix}-{category}-{i}"
            if category != "unrelated":
                memories.append(
                    {
                        "id": f"m-{key}",
                        "person": owner,
                        "content": f"{key} 에 관한 기억",
                        "scope": "user",
                        "kind": "experience" if category == "time" else "fact",
                        "importance": 2,
                        "occurred_start": "2026-10-01" if category == "time" else None,
                        "occurred_end": None,
                    }
                )
            questions.append(
                {
                    "id": f"q-{key}",
                    "person": owner,
                    "category": category,
                    "split": "report",
                    "group": f"group-{key}",
                    "question": f"{key} 에 관한 질문",
                    "previous": f"{key} 앞선 발화" if category == "multi_turn" else None,
                    "gold": [] if category == "unrelated" else [f"m-{key}"],
                }
            )
    return {
        "today": "2026-11-07",
        "people": [{"id": owner}],
        "memories": memories,
        "questions": questions,
    }


@pytest.fixture
def report() -> dict:
    return _corpus("new", 25)


@pytest.fixture
def historical() -> dict:
    return _corpus("old", 1)


def test_shipped_report_set_is_independent() -> None:
    directory = Path(__file__).resolve().parents[1] / "evals" / "memory_recall"
    report = json.loads((directory / "independent_ko.json").read_text())
    historical = json.loads((directory / "dataset.json").read_text())
    validate_corpus(report, historical)


def test_accepts_six_independent_categories(report: dict, historical: dict) -> None:
    validate_corpus(report, historical)


@pytest.mark.parametrize("historical_split", ["tune", "report"])
@pytest.mark.parametrize("field", ["previous", "question"])
def test_rejects_normalized_turn_overlap(
    report: dict, historical: dict, historical_split: str, field: str
) -> None:
    old = historical["questions"][0]
    old.update(question="Ａ 오래된 질의!", split=historical_split)
    new = next(q for q in report["questions"] if q["category"] == "multi_turn")
    new[field] = unicodedata.normalize("NFD", "a 오래 된 질의…")
    with pytest.raises(ValueError, match="overlaps the historical corpus"):
        validate_corpus(report, historical)


def test_rejects_historical_memory_with_new_id(report: dict, historical: dict) -> None:
    historical["memories"][0]["content"] = "사용자는 抹茶 라떼를 좋아한다."
    report["memories"][0]["content"] = "사용자는 抹茶라떼를 좋아한다!"
    with pytest.raises(ValueError, match="memory content overlaps"):
        validate_corpus(report, historical)


def test_rejects_historical_id_with_new_content(report: dict, historical: dict) -> None:
    report["memories"][0]["id"] = historical["memories"][0]["id"]
    with pytest.raises(ValueError, match="ID overlaps"):
        validate_corpus(report, historical)


@pytest.mark.parametrize(
    ("table", "changes", "error"),
    [
        ("questions", {"category": "typo"}, "category"),
        ("questions", {"split": "tune"}, "split"),
        ("questions", {"gold": []}, "gold list"),
        ("questions", {"gold": ["unknown"]}, "missing gold"),
        ("questions", {"person": "unknown"}, "owner"),
        ("questions", {"previous": "문맥"}, "previous turn"),
        ("questions", {"category": "multi_turn"}, "previous turn"),
        ("memories", {"kind": "unknown"}, "kind"),
        ("memories", {"scope": "shared"}, "scope"),
        ("memories", {"importance": True}, "importance"),
        ("memories", {"content": "…"}, "letters or numbers"),
        ("memories", {"occurred_start": "2026-02-30"}, "range"),
        ("memories", {"occurred_end": "2026-01-01"}, "date range"),
        (
            "memories",
            {"occurred_start": "2026-01-02", "occurred_end": "2026-01-01"},
            "date range",
        ),
        ("memories", {"kind": "experience", "occurred_start": "2027-01-01"}, "evaluation date"),
    ],
)
def test_rejects_malformed_records(
    report: dict, historical: dict, table: str, changes: dict, error: str
) -> None:
    report[table][0].update(changes)
    with pytest.raises(ValueError, match=error):
        validate_corpus(report, historical)


def test_rejects_foreign_gold_or_duplicate_gold(report: dict, historical: dict) -> None:
    report["people"].append({"id": "another-owner"})
    first = report["questions"][0]
    first["person"] = "another-owner"
    with pytest.raises(ValueError, match="wrong gold owner"):
        validate_corpus(report, historical)
    first["person"] = report["people"][0]["id"]
    first["gold"] *= 2
    with pytest.raises(ValueError, match="duplicate gold ID"):
        validate_corpus(report, historical)


def test_rejects_duplicate_question_id_and_text(report: dict, historical: dict) -> None:
    first, second = report["questions"][:2]
    old_id = second["id"]
    second["id"] = first["id"]
    with pytest.raises(ValueError, match="duplicate report ID"):
        validate_corpus(report, historical)
    second.update(id=old_id, question=first["question"] + "?")
    with pytest.raises(ValueError, match="duplicate report query"):
        validate_corpus(report, historical)


def test_repeated_gold_cannot_inflate_independent_sample_size(
    report: dict, historical: dict
) -> None:
    first, second = report["questions"][:2]
    second["gold"] = first["gold"]
    with pytest.raises(ValueError, match="shared gold"):
        validate_corpus(report, historical)
    second["group"] = first["group"]
    with pytest.raises(ValueError, match="25 independent question groups"):
        validate_corpus(report, historical)
