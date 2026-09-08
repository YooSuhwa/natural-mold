"""Exact-selection tests for Playwright JSON receipts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from e2e_runner_playwright import (  # noqa: E402
    CANONICAL_LIVE_CASES,
    MAX_PLAYWRIGHT_NODES,
    MAX_PLAYWRIGHT_RECEIPT_BYTES,
    MAX_PLAYWRIGHT_RESULTS,
    MAX_PLAYWRIGHT_STRING_LENGTH,
    MAX_PLAYWRIGHT_SUITE_DEPTH,
    MAX_PLAYWRIGHT_SUITES,
    MAX_PLAYWRIGHT_TITLE_LENGTH,
    PlaywrightNode,
    PlaywrightReceiptError,
    assert_exact_selection,
    canonical_live_cases_json,
    canonical_live_nodes,
    canonical_live_title_filter,
    parse_live_cases,
    parse_playwright_json,
)


def test_json_list_parses_exact_leaf_identity(tmp_path: Path) -> None:
    """Given a JSON list receipt, when parsed, then only leaf identities remain."""
    receipt = tmp_path / "list.json"
    receipt.write_text(
        json.dumps(
            {
                "suites": [
                    {
                        "file": "/mirror/frontend/e2e/builder.spec.ts",
                        "specs": [
                            {
                                "title": "leaf title",
                                "tests": [
                                    {
                                        "projectName": "live-manual",
                                        "expectedStatus": "passed",
                                        "results": [],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        )
    )

    nodes = parse_playwright_json(receipt)

    assert nodes == (PlaywrightNode("live-manual", "e2e/builder.spec.ts", "leaf title"),)


@pytest.mark.parametrize(
    ("active_results", "skipped_results"),
    [
        ([], []),
        ([{"status": "passed"}], [{"status": "skipped"}]),
    ],
)
def test_receipt_omits_tests_declared_skipped(
    tmp_path: Path,
    active_results: list[dict[str, str]],
    skipped_results: list[dict[str, str]],
) -> None:
    """Given list or execution skips, when parsed, then only executable identities remain."""
    receipt = tmp_path / "list.json"
    receipt.write_text(
        json.dumps(
            {
                "suites": [
                    {
                        "file": "e2e/drafts.spec.ts",
                        "specs": [
                            {
                                "title": "active",
                                "tests": [
                                    {
                                        "projectName": "scripted-full",
                                        "expectedStatus": "passed",
                                        "results": active_results,
                                    }
                                ],
                            },
                            {
                                "title": "draft",
                                "tests": [
                                    {
                                        "projectName": "scripted-full",
                                        "expectedStatus": "skipped",
                                        "annotations": [{"type": "skip"}],
                                        "results": skipped_results,
                                    }
                                ],
                            },
                        ],
                    }
                ]
            }
        )
    )

    nodes = parse_playwright_json(receipt)

    assert nodes == (PlaywrightNode("scripted-full", "e2e/drafts.spec.ts", "active"),)


@pytest.mark.parametrize("field", ["expectedStatus", "resultStatus"])
def test_receipt_rejects_unknown_playwright_status(tmp_path: Path, field: str) -> None:
    """Given an unknown status, when parsed, then receipt validation fails closed."""
    test = {
        "projectName": "scripted-full",
        "expectedStatus": "passed",
        "results": [{"status": "passed"}],
    }
    if field == "expectedStatus":
        test["expectedStatus"] = "customer-secret-status"
    else:
        test["results"] = [{"status": "customer-secret-status"}]
    receipt = tmp_path / f"{field}.json"
    receipt.write_text(
        json.dumps(
            {
                "suites": [
                    {
                        "file": "e2e/a.spec.ts",
                        "specs": [{"title": "leaf", "tests": [test]}],
                    }
                ]
            }
        )
    )

    with pytest.raises(PlaywrightReceiptError, match="invalid_playwright_status"):
        parse_playwright_json(receipt)


@pytest.mark.parametrize("field", ["expectedStatus", "resultStatus"])
def test_receipt_rejects_missing_playwright_status(tmp_path: Path, field: str) -> None:
    """Given an omitted status, when parsed, then receipt validation fails closed."""
    test = {
        "projectName": "scripted-full",
        "expectedStatus": "passed",
        "results": [{"status": "passed"}],
    }
    if field == "expectedStatus":
        del test["expectedStatus"]
    else:
        test["results"] = [{}]
    receipt = tmp_path / f"missing-{field}.json"
    receipt.write_text(
        json.dumps(
            {
                "suites": [
                    {
                        "file": "e2e/a.spec.ts",
                        "specs": [{"title": "leaf", "tests": [test]}],
                    }
                ]
            }
        )
    )

    with pytest.raises(PlaywrightReceiptError, match="missing_playwright_status"):
        parse_playwright_json(receipt)


@pytest.mark.parametrize("result_status", ["passed", "failed", "timedOut", "interrupted"])
def test_receipt_rejects_executed_result_declared_skipped(
    tmp_path: Path, result_status: str
) -> None:
    """Given a declared skip that executed, when parsed, then validation fails closed."""
    receipt = tmp_path / f"contradictory-{result_status}.json"
    receipt.write_text(
        json.dumps(
            {
                "suites": [
                    {
                        "file": "e2e/a.spec.ts",
                        "specs": [
                            {
                                "title": "leaf",
                                "tests": [
                                    {
                                        "projectName": "scripted-full",
                                        "expectedStatus": "skipped",
                                        "results": [{"status": result_status}],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        )
    )

    with pytest.raises(PlaywrightReceiptError, match="contradictory_playwright_status"):
        parse_playwright_json(receipt)


def test_receipt_accepts_playwright_interrupted_status(tmp_path: Path) -> None:
    """Given Playwright's supported interrupted status, when parsed, then identity is retained."""
    receipt = tmp_path / "interrupted.json"
    receipt.write_text(
        json.dumps(
            {
                "suites": [
                    {
                        "file": "e2e/a.spec.ts",
                        "specs": [
                            {
                                "title": "leaf",
                                "tests": [
                                    {
                                        "projectName": "scripted-full",
                                        "expectedStatus": "interrupted",
                                        "results": [{"status": "interrupted"}],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        )
    )

    assert parse_playwright_json(receipt) == (
        PlaywrightNode("scripted-full", "e2e/a.spec.ts", "leaf"),
    )


def test_live_cases_require_exactly_the_canonical_number_of_unique_cases() -> None:
    """Given a broadened case set, when parsed, then the live gate fails."""
    raw = json.dumps([{"spec": "e2e/a.spec.ts", "title": "one"}])

    with pytest.raises(PlaywrightReceiptError, match="live_case_count"):
        parse_live_cases(raw, "live-manual")


def test_selection_rejects_project_mismatch() -> None:
    """Given the right title in the wrong project, when checked, then it fails."""
    expected = (PlaywrightNode("live-manual", "e2e/a.spec.ts", "one"),)
    selected = (PlaywrightNode("scripted-full", "e2e/a.spec.ts", "one"),)

    with pytest.raises(PlaywrightReceiptError, match="selection_mismatch"):
        assert_exact_selection(selected, expected)


def test_json_list_rejects_duplicate_physical_node(tmp_path: Path) -> None:
    """Given duplicate leaf identities, when parsed, then the receipt is rejected."""
    receipt = tmp_path / "list.json"
    duplicate = {
        "title": "same leaf",
        "tests": [
            {
                "projectName": "live-manual",
                "expectedStatus": "passed",
                "results": [],
            }
        ],
    }
    receipt.write_text(
        json.dumps(
            {
                "suites": [
                    {
                        "file": "/mirror/frontend/e2e/builder.spec.ts",
                        "specs": [duplicate, duplicate],
                    }
                ]
            }
        )
    )

    with pytest.raises(PlaywrightReceiptError, match="duplicate_playwright_node"):
        parse_playwright_json(receipt)


def test_receipt_rejects_oversized_regular_file_before_json_decode(tmp_path: Path) -> None:
    """Given an oversized receipt, when parsed, then the byte cap rejects it."""
    receipt = tmp_path / "oversized.json"
    receipt.write_bytes(b"x" * (MAX_PLAYWRIGHT_RECEIPT_BYTES + 1))

    with pytest.raises(PlaywrightReceiptError, match="playwright_receipt_too_large"):
        parse_playwright_json(receipt)


def test_receipt_rejects_symlink_even_when_target_is_valid(tmp_path: Path) -> None:
    """Given a receipt symlink, when parsed, then no-follow receipt I/O rejects it."""
    target = tmp_path / "target.json"
    target.write_text(json.dumps({"suites": []}))
    receipt = tmp_path / "receipt.json"
    receipt.symlink_to(target)

    with pytest.raises(PlaywrightReceiptError, match="unsafe_playwright_receipt_file"):
        parse_playwright_json(receipt)


def test_receipt_rejects_suite_nesting_beyond_depth_limit(tmp_path: Path) -> None:
    """Given a deeply nested receipt, when parsed, then traversal stays bounded."""
    nested = '{"suites":[]}'
    for _ in range(MAX_PLAYWRIGHT_SUITE_DEPTH + 1):
        nested = f'{{"suites":[{nested}]}}'
    receipt = tmp_path / "deep.json"
    receipt.write_text(f'{{"suites":[{nested}]}}')

    with pytest.raises(PlaywrightReceiptError, match="playwright_suite_depth_limit"):
        parse_playwright_json(receipt)


def test_receipt_rejects_title_beyond_title_limit(tmp_path: Path) -> None:
    """Given an oversized leaf title, when parsed, then identity construction rejects it."""
    receipt = tmp_path / "huge-title.json"
    receipt.write_text(
        json.dumps(
            {
                "suites": [
                    {
                        "file": "e2e/builder.spec.ts",
                        "specs": [
                            {
                                "title": "x" * (MAX_PLAYWRIGHT_TITLE_LENGTH + 1),
                                "tests": [
                                    {
                                        "projectName": "scripted-full",
                                        "expectedStatus": "passed",
                                        "results": [],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        )
    )

    with pytest.raises(PlaywrightReceiptError, match="playwright_title_too_long"):
        parse_playwright_json(receipt)


def test_receipt_rejects_string_and_node_id_beyond_limits(tmp_path: Path) -> None:
    """Given oversized identity fields, when parsed, then each identity bound is enforced."""
    string_receipt = tmp_path / "huge-project.json"
    string_receipt.write_text(
        json.dumps(
            {
                "suites": [
                    {
                        "file": "e2e/builder.spec.ts",
                        "specs": [
                            {
                                "title": "leaf",
                                "tests": [
                                    {
                                        "projectName": "x" * (MAX_PLAYWRIGHT_STRING_LENGTH + 1),
                                        "expectedStatus": "passed",
                                        "results": [],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        )
    )
    node_id_receipt = tmp_path / "huge-node-id.json"
    node_id_receipt.write_text(
        json.dumps(
            {
                "suites": [
                    {
                        "file": "e2e/builder.spec.ts",
                        "specs": [
                            {
                                "title": "x" * MAX_PLAYWRIGHT_TITLE_LENGTH,
                                "tests": [
                                    {
                                        "projectName": "p" * MAX_PLAYWRIGHT_TITLE_LENGTH,
                                        "expectedStatus": "passed",
                                        "results": [],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        )
    )

    with pytest.raises(PlaywrightReceiptError, match="playwright_string_too_long"):
        parse_playwright_json(string_receipt)
    with pytest.raises(PlaywrightReceiptError, match="playwright_node_id_too_long"):
        parse_playwright_json(node_id_receipt)


def test_receipt_rejects_node_explosion(tmp_path: Path) -> None:
    """Given more leaves than the receipt cap, when parsed, then it fails closed."""
    receipt = tmp_path / "many-nodes.json"
    specs = [
        {
            "title": f"leaf {index}",
            "tests": [
                {
                    "projectName": "scripted-full",
                    "expectedStatus": "passed",
                    "results": [],
                }
            ],
        }
        for index in range(MAX_PLAYWRIGHT_NODES + 1)
    ]
    receipt.write_text(json.dumps({"suites": [{"file": "e2e/a.spec.ts", "specs": specs}]}))

    with pytest.raises(PlaywrightReceiptError, match="playwright_node_limit"):
        parse_playwright_json(receipt)


def test_receipt_rejects_suite_and_result_explosions(tmp_path: Path) -> None:
    """Given excessive suites or results, when parsed, then both traversal caps apply."""
    suite_receipt = tmp_path / "many-suites.json"
    suite_receipt.write_text(json.dumps({"suites": [{"specs": []}] * (MAX_PLAYWRIGHT_SUITES + 1)}))
    result_receipt = tmp_path / "many-results.json"
    result_receipt.write_text(
        json.dumps(
            {
                "suites": [
                    {
                        "file": "e2e/a.spec.ts",
                        "specs": [
                            {
                                "title": f"leaf {index}",
                                "tests": [
                                    {
                                        "projectName": "scripted-full",
                                        "expectedStatus": "skipped",
                                        "results": [{"status": "skipped"}],
                                    }
                                ],
                            }
                            for index in range(MAX_PLAYWRIGHT_RESULTS + 1)
                        ],
                    }
                ]
            }
        )
    )

    with pytest.raises(PlaywrightReceiptError, match="playwright_suite_limit"):
        parse_playwright_json(suite_receipt)
    with pytest.raises(PlaywrightReceiptError, match="playwright_result_limit"):
        parse_playwright_json(result_receipt)


def test_receipt_rejects_ambiguous_multiple_results(tmp_path: Path) -> None:
    """Given multiple result records for one test, when parsed, then it is ambiguous."""
    receipt = tmp_path / "ambiguous-results.json"
    receipt.write_text(
        json.dumps(
            {
                "suites": [
                    {
                        "file": "e2e/builder.spec.ts",
                        "specs": [
                            {
                                "title": "leaf",
                                "tests": [
                                    {
                                        "projectName": "scripted-full",
                                        "expectedStatus": "passed",
                                        "results": [
                                            {"status": "passed"},
                                            {"status": "passed"},
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        )
    )

    with pytest.raises(PlaywrightReceiptError, match="ambiguous_playwright_results"):
        parse_playwright_json(receipt)


def test_canonical_live_contract_cannot_be_redefined_by_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E2E_LIVE_CASES_JSON", '[{"spec":"e2e/forged.spec.ts","title":"x"}]')
    monkeypatch.setenv("E2E_LIVE_TITLE_FILTER", ".*")

    assert len(canonical_live_nodes("live-manual")) == 5
    assert json.loads(canonical_live_cases_json()) == [
        {"spec": spec, "title": title} for spec, title in CANONICAL_LIVE_CASES
    ]
    assert canonical_live_title_filter().endswith("$")
    assert ".*" not in canonical_live_title_filter()
