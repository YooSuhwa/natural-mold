# pyright: reportMissingImports=false
# Runs in its own venv (schift-ko-pii==0.4.0); the model code patches transformers globally.
"""schift-ko-pii on the same K-PII-Bench sample and scorer as bench.py (own process).

Usage: HF_HUB_OFFLINE=1 python bench_schift.py <test_track_a.jsonl> <model_dir> <out.json>
       [size] [extended]
The model is loaded from <model_dir> only; hub downloads are redirected to that folder.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

SEED = "moldy-2026-11-05"
GOLD = {
    "PERSON": "PERSON",
    "EMAIL_ADDRESS": "EMAIL",
    "KR_PHONE_NUMBER": "PHONE",
    "KR_RRN": "RRN",
    "KR_BANK_ACCOUNT": "ACCOUNT",
    "KR_DRIVER_LICENSE": "DRIVER_LICENSE",
    "KR_PASSPORT": "PASSPORT",
    "KR_NHIS": "MEDICAL_INSURANCE",
    "KR_VEHICLE_PLATE": "VEHICLE",
    "KR_LOCATION": "ADDRESS",
    "CREDIT_CARD": "CARD",
    "IP_ADDRESS": "IP",
    "URL": "URL",
}
PRED = {
    "private_person": "PERSON",
    "private_email": "EMAIL",
    "private_phone": "PHONE",
    "resident_id": "RRN",
    "account_number": "ACCOUNT",
    "private_address": "ADDRESS",
    "private_url": "URL",
}
IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def sample(path: str, size: int) -> list[dict]:
    docs = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]
    kopii = {
        d["doc_id"]
        for d in sorted(
            docs, key=lambda d: (hashlib.sha256(d["doc_id"].encode()).hexdigest(), d["doc_id"])
        )[:500]
    }
    rest = [d for d in docs if d["doc_id"] not in kopii]
    rest.sort(key=lambda d: hashlib.sha256((SEED + d["doc_id"]).encode()).hexdigest())
    return rest[:size]


def match(gold, pred, strict):
    used, tp = set(), 0
    for gs, ge in gold:
        for i, (ps, pe) in enumerate(pred):
            if i in used:
                continue
            if ((ps, pe) == (gs, ge)) if strict else (ps < ge and gs < pe):
                used.add(i)
                tp += 1
                break
    return tp, len(pred) - tp, len(gold) - tp


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(p, 3),
        "recall": round(r, 3),
        "f1": round(f, 3),
    }


def main() -> None:
    path, model_dir, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    size = int(sys.argv[4]) if len(sys.argv) > 4 else 3000
    extended = len(sys.argv) > 5 and sys.argv[5] == "extended"

    import importlib

    import huggingface_hub

    det = importlib.import_module("schift_ko_pii.detect")

    det.HF_MODEL_ID = model_dir
    huggingface_hub.hf_hub_download = lambda _repo, filename, **_kw: str(Path(model_dir) / filename)
    labels_seen: Counter = Counter()

    docs = sample(path, size)
    det.detect("워밍업 홍길동 010-1234-5678", extended=extended)
    counts = {m: defaultdict(Counter) for m in ("strict", "overlap")}
    latencies = []
    for doc in docs:
        text = doc["text"]
        t0 = time.perf_counter()
        results = det.detect(text, extended=extended)
        latencies.append((time.perf_counter() - t0) * 1000)
        gold = defaultdict(list)
        for e in doc["entities"]:
            if e["type"] in GOLD:
                gold[GOLD[e["type"]]].append((e["start"], e["end"]))
        pred = defaultdict(set)
        for r in results:
            labels_seen[r["label"]] += 1
            cat = PRED.get(r["label"])
            if cat == "URL" and IPV4.match(r["text"]):
                cat = "IP"
            if cat:
                pred[cat].add((r["start"], r["end"]))
        for cat in set(gold) | set(pred):
            g, p = sorted(gold.get(cat, [])), sorted(pred.get(cat, set()))
            for m in ("strict", "overlap"):
                tp, fp, fn = match(g, p, strict=m == "strict")
                counts[m][cat].update(tp=tp, fp=fp, fn=fn)
    out = {
        "model_dir": model_dir,
        "extended": extended,
        "sample_size": len(docs),
        "labels_seen": dict(labels_seen),
    }
    for m, per in counts.items():
        total = Counter()
        for v in per.values():
            total.update(v)
        out[m] = {
            "micro": prf(total["tp"], total["fp"], total["fn"]),
            "per_category": {c: prf(v["tp"], v["fp"], v["fn"]) for c, v in sorted(per.items())},
        }
    q = statistics.quantiles(latencies, n=100)
    out["latency_ms"] = {
        "p50": round(q[49], 1),
        "p95": round(q[94], 1),
        "mean": round(statistics.mean(latencies), 1),
    }
    Path(out_path).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("latency_ms",)}), out["strict"]["micro"], flush=True)


if __name__ == "__main__":
    main()
