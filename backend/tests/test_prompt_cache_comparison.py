import hashlib
import json
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.compare_prompt_cache import (
    Arm,
    BillingSlice,
    Observation,
    ReconciledBilling,
    compare,
    main,
)


def observations(n: int = 30, *, fixture: bool = False) -> list[Observation]:
    rows = []
    arms: Sequence[Arm] = ("key_omitted", "key_supplied")
    for i in range(1, n + 1):
        for arm in arms:
            rows.append(
                Observation(
                    sequence=i,
                    scope="test-scope",
                    model="exact-snapshot",
                    arm=arm,
                    request_sha256=hashlib.sha256(str(i).encode("ascii")).hexdigest(),
                    request_id=f"{arm}-{i}",
                    source="fixture" if fixture else "provider_usage",
                    input_tokens=2000,
                    cached_input_tokens=1000 if arm == "key_omitted" else 1500,
                    ttft_ms=Decimal("105") if arm == "key_supplied" else Decimal("100"),
                )
            )
    return rows


def billing(rows: Sequence[Observation]) -> ReconciledBilling:
    def attributed(arm: Arm, amount: str) -> BillingSlice:
        return BillingSlice(
            reference=f"invoice-line-{arm}",
            input_usd=Decimal(amount),
            request_ids=[r.request_id for r in rows if r.arm == arm],
        )

    return ReconciledBilling(
        scope="test-scope",
        model="exact-snapshot",
        key_omitted=attributed("key_omitted", "3.00"),
        key_supplied=attributed("key_supplied", "2.70"),
    )


def test_verdict_uses_billing_and_accepts_exact_thresholds() -> None:
    rows = observations()
    result = compare(rows, Decimal(2), Decimal(1), billing(rows))
    assert result["estimated_mean_input_cost_change_pct"] == (Decimal(-100) / 6)
    assert result["reconciled_mean_input_cost_change_pct"] == -10
    assert result["p95_ttft_change_pct"] == 5
    assert result["verdict"] == "targets_met_on_supplied_evidence"


def test_no_claim_without_independent_charges() -> None:
    result = compare(observations(), Decimal(2), Decimal(1))
    assert result["billing_evidence"] == "absent"
    assert result["verdict"] == "insufficient_billing_evidence"
    assert result["reconciled_mean_input_cost_change_pct"] is None
    rows = observations(fixture=True)
    assert compare(rows, Decimal(2), Decimal(1))["verdict"] == "fixture_only"
    with pytest.raises(ValueError, match="billing must correspond"):
        compare(rows, Decimal(2), Decimal(1), billing(rows))


def test_mixed_scope_model_source_or_incomplete_or_out_of_order_samples_rejected() -> None:
    rows = observations(2)
    for changes in (
        {"model": "other"},
        {"scope": "other"},
        {"source": "fixture"},
        {"request_sha256": "a" * 64},
        {"input_tokens": 2001},
        {"request_id": rows[0].request_id},
    ):
        changed = [*rows[:-1], rows[-1].model_copy(update=changes)]
        with pytest.raises(ValueError, match="complete pairs|pair 2|unique request IDs"):
            compare(changed, Decimal(1), Decimal(0))
    for invalid in ([], rows[:-1], list(reversed(rows))):
        with pytest.raises(ValueError, match="complete pairs|consecutive"):
            compare(invalid, Decimal(1), Decimal(0))


def test_checks_billing_scope_and_exact_observed_request_ids() -> None:
    rows = observations()
    for target in ("scope", "model", "reference", "request_ids"):
        charges = billing(rows)
        if target == "scope":
            charges.scope = "different"
        elif target == "model":
            charges.model = "different"
        elif target == "reference":
            charges.key_omitted.reference = charges.key_supplied.reference
        else:
            charges.key_supplied.request_ids.append(rows[0].request_id)
        with pytest.raises(ValueError, match="billing must correspond|distinct attributed|match"):
            compare(rows, Decimal(1), Decimal(0), charges)


def test_small_set_zero_baseline_and_worse_billing() -> None:
    rows = observations(29)
    assert compare(rows, Decimal(1), Decimal(0), billing(rows))["verdict"] == "insufficient_pairs"
    rows = observations()
    charges = billing(rows)
    charges.key_omitted.input_usd = Decimal(0)
    assert (
        compare(rows, Decimal(1), Decimal(0), charges)["verdict"] == "insufficient_billing_evidence"
    )
    charges = billing(rows)
    charges.key_supplied.input_usd = Decimal("3.10")
    assert (
        compare(rows, Decimal(1), Decimal(0), charges)["verdict"]
        == "targets_missed_on_supplied_evidence"
    )


def test_p95_is_nearest_rank_and_regression_fails_verdict() -> None:
    rows = observations()
    on = [row for row in rows if row.arm == "key_supplied"]
    on[-1].ttft_ms = Decimal("10000")
    assert compare(rows, Decimal(2), Decimal(0), billing(rows))["p95_ttft_change_pct"] == 5
    on[-2].ttft_ms = Decimal("106")
    result = compare(rows, Decimal(2), Decimal(0), billing(rows))
    assert result["p95_ttft_change_pct"] == 6
    assert result["verdict"] == "targets_missed_on_supplied_evidence"


def test_reject_negative_or_nonfinite_rate_and_invalid_counts() -> None:
    row = observations(1)[0]
    for invalid in ("Infinity", "NaN", "-1"):
        with pytest.raises(ValueError, match="finite nonnegative"):
            compare([row], Decimal(1), Decimal(invalid))
    for changes in ({"cached_input_tokens": 2001}, {"input_tokens": True}, {"ttft_ms": 0}):
        with pytest.raises(ValidationError):
            Observation.model_validate(row.model_dump() | changes)


def test_cli_reads_jsonl_and_reports_estimate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = observations(1, fixture=True)
    path = tmp_path / "example.jsonl"
    path.write_text("\n".join(row.model_dump_json() for row in rows), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compare_prompt_cache",
            str(path),
            "--input-usd-per-million",
            "2",
            "--cached-input-usd-per-million",
            "1",
        ],
    )
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["verdict"] == "fixture_only"
    assert result["arms"]["key_omitted"]["estimated_mean_input_usd"] == "0.003"
    assert result["arms"]["key_supplied"]["cached_input_tokens"] == 1500
