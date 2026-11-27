"""Compare otherwise identical OpenAI requests with a prompt cache key supplied or omitted.

Run from backend with python -m scripts.compare_prompt_cache --help. Each JSONL line contains
arm, sequence (one-based within each arm), scope (opaque test-organization identifier), model
(exact model snapshot), request_sha256 (digest of the same request body excluding the cache
key), request_id, source (provider_usage or fixture), input_tokens (including cached tokens),
cached_input_tokens, and ttft_ms (request start to first content token). Record requests in order.

Optional billing JSON contains scope, model, key_omitted and key_supplied. Each arm contains
input_usd (input charges only), reference (distinct provider billing slice), and request_ids
(the matched test requests). The operator must reconcile these slices with provider records.
This offline comparison does not authenticate uploaded records or call the provider.
"""

import argparse
import json
import math
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

type Arm = Literal["key_omitted", "key_supplied"]
type Amount = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
type Label = Annotated[str, Field(min_length=1)]
type TokenCount = Annotated[int, Field(ge=0, strict=True)]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)


class Observation(Record):
    arm: Arm
    sequence: Annotated[int, Field(ge=1, strict=True)]
    scope: Label
    model: Label
    request_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    request_id: Label
    source: Literal["provider_usage", "fixture"]
    input_tokens: TokenCount
    cached_input_tokens: TokenCount
    ttft_ms: Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]

    @model_validator(mode="after")
    def check_cached_tokens(self) -> Self:
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens exceeds input_tokens")
        return self


class BillingSlice(Record):
    input_usd: Amount
    reference: Label
    request_ids: list[Label]


class ReconciledBilling(Record):
    scope: Label
    model: Label
    key_omitted: BillingSlice
    key_supplied: BillingSlice


def percent_change(initial: Decimal, final: Decimal) -> Decimal | None:
    return (final - initial) * 100 / initial if initial else None


def compare(
    observations: Iterable[Observation],
    input_rate: Decimal,
    cached_rate: Decimal,
    billing: ReconciledBilling | None = None,
) -> dict[str, object]:
    if not all(rate.is_finite() and rate >= 0 for rate in (input_rate, cached_rate)):
        raise ValueError("rates must be finite nonnegative USD amounts per million input tokens")

    arms: dict[Arm, list[Observation]] = {"key_omitted": [], "key_supplied": []}
    request_ids: set[str] = set()
    dataset: set[tuple[str, str, str]] = set()
    for row in observations:
        arm = arms[row.arm]
        if row.sequence != len(arm) + 1 or row.request_id in request_ids:
            raise ValueError("each arm needs consecutive ordered requests and unique request IDs")
        arm.append(row)
        request_ids.add(row.request_id)
        dataset.add((row.scope, row.model, row.source))

    omitted, supplied = arms["key_omitted"], arms["key_supplied"]
    if not omitted or len(omitted) != len(supplied) or len(dataset) != 1:
        raise ValueError("provide complete pairs from one scope, exact model and evidence source")
    for before, after in zip(omitted, supplied, strict=True):
        if (before.request_sha256, before.input_tokens) != (
            after.request_sha256,
            after.input_tokens,
        ):
            raise ValueError(
                f"request digest or input token count differs at pair {before.sequence}"
            )
    scope, model, source = dataset.pop()
    n = len(omitted)

    if billing:
        if source != "provider_usage" or (scope, model) != (billing.scope, billing.model):
            raise ValueError("billing must correspond to this provider usage scope and model")
        if billing.key_omitted.reference == billing.key_supplied.reference:
            raise ValueError("use distinct attributed input charge references for the two arms")

    results: dict[str, dict[str, int | Decimal]] = {}
    for arm_name, samples in arms.items():
        total = sum(sample.input_tokens for sample in samples)
        cached = sum(sample.cached_input_tokens for sample in samples)
        estimated = ((total - cached) * input_rate + cached * cached_rate) / 1_000_000
        timings = sorted(sample.ttft_ms for sample in samples)
        results[arm_name] = {
            "input_tokens": total,
            "cached_input_tokens": cached,
            "estimated_mean_input_usd": estimated / n,
            "p95_ttft_ms": timings[math.ceil(n * 95 / 100) - 1],
        }
        if billing:
            charges = billing.key_omitted if arm_name == "key_omitted" else billing.key_supplied
            if sorted(charges.request_ids) != sorted(sample.request_id for sample in samples):
                raise ValueError(f"billing request IDs must match {arm_name} samples exactly")
            results[arm_name]["reconciled_mean_input_usd"] = charges.input_usd / n

    def change(metric: str) -> Decimal | None:
        return percent_change(
            Decimal(results["key_omitted"][metric]), Decimal(results["key_supplied"][metric])
        )

    latency_change = change("p95_ttft_ms")
    billed_change = change("reconciled_mean_input_usd") if billing else None
    if source == "fixture":
        verdict = "fixture_only"
    elif n < 30:
        verdict = "insufficient_pairs"
    elif billed_change is None:
        verdict = "insufficient_billing_evidence"
    elif billed_change <= -10 and latency_change is not None and latency_change <= 5:
        verdict = "targets_met_on_supplied_evidence"
    else:
        verdict = "targets_missed_on_supplied_evidence"

    return {
        "model": model,
        "source": source,
        "pairs": n,
        "billing_evidence": "operator_reconciled" if billing else "absent",
        "input_usd_per_million": input_rate,
        "cached_input_usd_per_million": cached_rate,
        "arms": results,
        "estimated_mean_input_cost_change_pct": change("estimated_mean_input_usd"),
        "reconciled_mean_input_cost_change_pct": billed_change,
        "p95_ttft_change_pct": latency_change,
        "verdict": verdict,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("observations", type=Path, help="JSONL of individually identified requests")
    parser.add_argument("--input-usd-per-million", required=True, help="uncached input price")
    parser.add_argument("--cached-input-usd-per-million", required=True, help="cached input price")
    parser.add_argument("--billing", type=Path, help="operator-reconciled input charges JSON")
    args = parser.parse_args()
    try:
        input_rate = Decimal(args.input_usd_per_million)
        cached_rate = Decimal(args.cached_input_usd_per_million)
        billing = (
            ReconciledBilling.model_validate_json(args.billing.read_bytes())
            if args.billing
            else None
        )
        with args.observations.open(encoding="utf-8") as stream:
            rows = (Observation.model_validate_json(line) for line in stream if line.strip())
            result = compare(rows, input_rate, cached_rate, billing)
    except (ArithmeticError, OSError, ValueError) as exc:
        parser.exit(2, f"comparison failed: {exc}\n")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
