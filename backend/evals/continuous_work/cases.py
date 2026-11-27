"""Fixed fictitious quotations used by the paired ordinary-chat and goal evaluation."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

COLUMNS = ("company", "country", "price_krw", "source")
FILE_NAME = "comparison.csv"
REFERENCE = (
    ("가온상사", "KR", "13500", "https://quotes.example.invalid/GA/2026Q4"),
    ("누리유통", "KR", "27900", "https://quotes.example.invalid/NU/2026Q4"),
    ("다온산업", "KR", "31800", "https://quotes.example.invalid/DA/2026Q4"),
)


def as_csv(rows: tuple[tuple[str, ...], ...]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    return stream.getvalue()


def task_text() -> str:
    return (
        "확정한 세 업체 견적을 comparison.csv로 만들어 주세요. 업체별 국가, 원화 가격과 "
        "출처 주소를 아래 원본과 일치시켜 주세요. 행의 순서는 자유입니다.\n" + as_csv(REFERENCE)
    )


def goal_request() -> dict[str, object]:
    return {
        "title": "확정 견적 세 곳의 가격과 출처를 비교표로 보관",
        "expected_revision": 0,
        "criteria": [
            {
                "kind": "csv_table",
                "logical_path": FILE_NAME,
                "required_columns": list(COLUMNS),
                "unique_by": "company",
                "minimum_rows": len(REFERENCE),
                "column_equals": {"country": "KR"},
                "reference_rows": [dict(zip(COLUMNS, row, strict=True)) for row in REFERENCE],
            }
        ],
    }


def meets_task(csv_text: str | None) -> bool:
    """Independent answer key: compare downloaded content to the user's original quotes."""
    if csv_text is None:
        return False
    try:
        reader = csv.DictReader(io.StringIO(csv_text, newline=""), strict=True)
        if not reader.fieldnames or sorted(reader.fieldnames) != sorted(COLUMNS):
            return False
        rows = list(reader)
        if len(rows) != len(REFERENCE):
            return False
        return sorted(tuple(row.get(key) for key in COLUMNS) for row in rows) == sorted(REFERENCE)
    except (csv.Error, TypeError):
        return False


def with_cell(index: int, field: str, value: str) -> tuple[tuple[str, ...], ...]:
    rows = [list(row) for row in REFERENCE]
    rows[index][COLUMNS.index(field)] = value
    return tuple(tuple(row) for row in rows)


@dataclass(frozen=True)
class Scenario:
    slug: str
    first: str | None
    subsequent: str | None

    def csv_at(self, turn: int) -> str | None:
        if turn < 1:
            raise ValueError("turn is one-based")
        return self.first if turn == 1 else self.subsequent

    @property
    def user_requests_second(self) -> bool:
        return not meets_task(self.first)


_VALID = as_csv(REFERENCE)
_WRONG_PRICE = as_csv(with_cell(0, "price_krw", "13501"))
_WRONG_SOURCE = as_csv(with_cell(1, "source", "https://quotes.example.invalid/NU/2026Q3"))
_SWAPPED = [list(row) for row in REFERENCE]
_SWAPPED[0][3], _SWAPPED[1][3] = _SWAPPED[1][3], _SWAPPED[0][3]
_WRONG_PAIR = as_csv(tuple(tuple(row) for row in _SWAPPED))

SCENARIOS = (
    Scenario("valid", _VALID, _VALID),
    Scenario("reordered", as_csv(REFERENCE[::-1]), _VALID),
    Scenario("price_repaired", _WRONG_PRICE, _VALID),
    Scenario("source_repaired", _WRONG_SOURCE, _VALID),
    Scenario("wrong_price_persists", _WRONG_PRICE, _WRONG_PRICE),
    Scenario("swapped_sources_persist", _WRONG_PAIR, _WRONG_PAIR),
    Scenario("missing_file", None, None),
    Scenario("empty_file", "", ""),
)

# Additional document counterexamples for the direct evidence verifier. They are
# separate from the registered goals measured through the ordinary chat worker.
DOCUMENT_CASES: dict[str, str | None] = {
    "exact": _VALID,
    "order": as_csv(REFERENCE[::-1]),
    "wrong_value": _WRONG_PRICE,
    "wrong_source": _WRONG_SOURCE,
    "wrong_pair": _WRONG_PAIR,
    "url_path_case": as_csv(with_cell(0, "source", REFERENCE[0][3].lower())),
    "wrong_country": as_csv(with_cell(2, "country", "US")),
    "duplicate_vendor": as_csv(with_cell(1, "company", REFERENCE[0][0])),
    "missing_company": as_csv(REFERENCE[:2]),
    "missing_cell": as_csv(with_cell(1, "price_krw", "")),
    "empty": "",
    "no_file": None,
}
