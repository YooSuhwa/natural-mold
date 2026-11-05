# pyright: reportMissingImports=false
# Runs in the backend venv: the packs under test are app.guardrails.detectors.
"""Moldy guardrail detector packs on the same K-PII-Bench sample and scorer as bench.py.

Usage (from backend/): uv run python ../poc/korean-pii-bench/bench_packs.py \
       <test_track_a.jsonl> <out.json> [size] [packs]
packs defaults to "common,kr". SECRET findings have no gold type and are not scored.
kr_model needs the pii-detector service: GUARDRAIL_PII_MODEL_URL and _API_KEY.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench_schift import GOLD, match, prf, sample  # noqa: E402

from app.guardrails.model_pack import detect_texts  # noqa: E402

PRED = {
    "PERSON": "PERSON",
    "EMAIL": "EMAIL",
    "PHONE": "PHONE",
    "FAX": "PHONE",
    "RRN": "RRN",
    "ACCOUNT": "ACCOUNT",
    "DRIVER_LICENSE": "DRIVER_LICENSE",
    "PASSPORT": "PASSPORT",
    "MEDICAL_INSURANCE": "MEDICAL_INSURANCE",
    "VEHICLE": "VEHICLE",
    "ADDRESS": "ADDRESS",
    "CARD": "CARD",
    "IP": "IP",
    "URL": "URL",
}


def detect(text: str, packs: list[str]) -> list:
    return asyncio.run(detect_texts([text], packs))[0]


def main() -> None:
    path, out_path = sys.argv[1], sys.argv[2]
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 3000
    packs = sys.argv[4].split(",") if len(sys.argv) > 4 else ["common", "kr"]
    docs = sample(path, size)
    detect("워밍업 demo@example.com", packs)
    counts = {m: defaultdict(Counter) for m in ("strict", "overlap")}
    latencies = []
    for doc in docs:
        t0 = time.perf_counter()
        findings = detect(doc["text"], packs)
        latencies.append((time.perf_counter() - t0) * 1000)
        gold = defaultdict(list)
        for e in doc["entities"]:
            if e["type"] in GOLD:
                gold[GOLD[e["type"]]].append((e["start"], e["end"]))
        pred = defaultdict(set)
        for f in findings:
            if f.entity in PRED:
                pred[PRED[f.entity]].add((f.start, f.end))
        for cat in set(gold) | set(pred):
            g, p = sorted(gold.get(cat, [])), sorted(pred.get(cat, set()))
            for m in ("strict", "overlap"):
                tp, fp, fn = match(g, p, strict=m == "strict")
                counts[m][cat].update(tp=tp, fp=fp, fn=fn)
    out: dict = {"packs": packs, "sample_size": len(docs)}
    for m, per in counts.items():
        total = Counter()
        for v in per.values():
            total.update(v)
        out[m] = {
            "micro": prf(total["tp"], total["fp"], total["fn"]),
            "per_category": {c: prf(v["tp"], v["fp"], v["fn"]) for c, v in sorted(per.items())},
        }
    q = statistics.quantiles(latencies, n=100)
    out["latency_ms"] = {"p50": round(q[49], 2), "p95": round(q[94], 2)}
    Path(out_path).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out["latency_ms"], out["strict"]["micro"], out["overlap"]["micro"]["f1"], flush=True)


if __name__ == "__main__":
    main()
