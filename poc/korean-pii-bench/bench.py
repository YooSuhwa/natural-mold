"""Presidio built-in Korean recognizers vs the ko-pii Presidio plugin on K-PII-Bench Track A.

Usage: python bench.py <test_track_a.jsonl> <out.json> [sample_size] [setup,...]

Sample: documents chosen by SHA-256(seed + doc_id), excluding the 500 that ko-pii's own
external evaluation used (lowest SHA-256(doc_id)), to reduce tuning overlap.
Scoring: per category, strict (same start, end, category) and overlap (any overlap, same
category, one-to-one). Gold types with no detector in either setup are not scored.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
import time
from collections import Counter, defaultdict

import spacy
from ko_pii.integrations.presidio_plugin import KPiiRecognizer
from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
from presidio_analyzer.nlp_engine import SpacyNlpEngine
from presidio_analyzer.predefined_recognizers import (
    CreditCardRecognizer,
    EmailRecognizer,
    IpRecognizer,
    KrDriverLicenseRecognizer,
    KrFrnRecognizer,
    KrPassportRecognizer,
    KrRrnRecognizer,
    PhoneRecognizer,
    UrlRecognizer,
)

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
    "PERSON": "PERSON",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "PHONE",
    "KR_RRN": "RRN",
    "KR_BANK_ACCOUNT": "ACCOUNT",
    "KR_DRIVER_LICENSE": "DRIVER_LICENSE",
    "KR_PASSPORT": "PASSPORT",
    "US_PASSPORT": "PASSPORT",  # ko-pii maps its Korean passport label here
    "KR_MEDICAL_INSURANCE": "MEDICAL_INSURANCE",
    "KR_VEHICLE_PLATE": "VEHICLE",
    "KR_ADDRESS": "ADDRESS",
    "CREDIT_CARD": "CARD",
    "IP_ADDRESS": "IP",
    "URL": "URL",
}


class BlankKorean(SpacyNlpEngine):
    def load(self) -> None:
        self.nlp = {"ko": spacy.blank("xx")}


def generic() -> list:
    return [
        EmailRecognizer(supported_language="ko"),
        CreditCardRecognizer(supported_language="ko"),
        IpRecognizer(supported_language="ko"),
        UrlRecognizer(supported_language="ko"),
        PhoneRecognizer(supported_language="ko", supported_regions=("KR",)),
    ]


def presidio_kr() -> list:
    return [
        KrRrnRecognizer(),
        KrFrnRecognizer(),
        KrDriverLicenseRecognizer(),
        KrPassportRecognizer(),
    ]


SETUPS = {
    "A_presidio_kr": lambda: generic() + presidio_kr(),
    "B_presidio_kopii": lambda: generic() + [KPiiRecognizer()],
    "B0_kopii_only": lambda: [KPiiRecognizer()],
    "AB_both": lambda: generic() + presidio_kr() + [KPiiRecognizer()],
    "C_kopii_plus_presidio_rrn": lambda: [KPiiRecognizer(), KrRrnRecognizer()],
}


def analyzer(recognizers: list) -> AnalyzerEngine:
    registry = RecognizerRegistry(supported_languages=["ko"])
    for recognizer in recognizers:
        registry.add_recognizer(recognizer)
    engine = BlankKorean(models=[{"lang_code": "ko", "model_name": "blank"}])
    return AnalyzerEngine(nlp_engine=engine, registry=registry, supported_languages=["ko"])


def sample(path: str, size: int) -> list[dict]:
    docs = [json.loads(line) for line in open(path, encoding="utf-8")]
    kopii = {
        d["doc_id"]
        for d in sorted(
            docs, key=lambda d: (hashlib.sha256(d["doc_id"].encode()).hexdigest(), d["doc_id"])
        )[:500]
    }
    rest = [d for d in docs if d["doc_id"] not in kopii]
    rest.sort(key=lambda d: hashlib.sha256((SEED + d["doc_id"]).encode()).hexdigest())
    return rest[:size]


def match(gold: list, pred: list, strict: bool) -> tuple[int, int, int]:
    used: set[int] = set()
    tp = 0
    for gs, ge in gold:
        for i, (ps, pe) in enumerate(pred):
            if i in used:
                continue
            ok = (ps, pe) == (gs, ge) if strict else ps < ge and gs < pe
            if ok:
                used.add(i)
                tp += 1
                break
    return tp, len(pred) - tp, len(gold) - tp


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3)}


def run(name: str, docs: list[dict], threshold: float) -> dict:
    engine = analyzer(SETUPS[name]())
    engine.analyze(text="워밍업 demo@example.com", language="ko")
    counts = {mode: defaultdict(Counter) for mode in ("strict", "overlap")}
    latencies = []
    for doc in docs:
        text = doc["text"]
        t0 = time.perf_counter()
        results = engine.analyze(text=text, language="ko", score_threshold=threshold)
        latencies.append((time.perf_counter() - t0) * 1000)
        gold = defaultdict(list)
        for e in doc["entities"]:
            if e["type"] in GOLD:
                gold[GOLD[e["type"]]].append((e["start"], e["end"]))
        pred = defaultdict(set)
        for r in results:
            if r.entity_type in PRED:
                pred[PRED[r.entity_type]].add((r.start, r.end))
        for cat in set(gold) | set(pred):
            g = sorted(gold.get(cat, []))
            p = sorted(pred.get(cat, set()))
            for mode in ("strict", "overlap"):
                tp, fp, fn = match(g, p, strict=mode == "strict")
                counts[mode][cat].update(tp=tp, fp=fp, fn=fn)
    out: dict = {"threshold": threshold}
    for mode, per in counts.items():
        cats = {c: prf(v["tp"], v["fp"], v["fn"]) for c, v in sorted(per.items())}
        total = Counter()
        for v in per.values():
            total.update(v)
        out[mode] = {"micro": prf(total["tp"], total["fp"], total["fn"]), "per_category": cats}
    q = statistics.quantiles(latencies, n=100)
    out["latency_ms"] = {"p50": round(q[49], 2), "p95": round(q[94], 2), "mean": round(statistics.mean(latencies), 2)}
    return out


def main() -> None:
    path, out_path = sys.argv[1], sys.argv[2]
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 3000
    docs = sample(path, size)
    gold_counts = Counter(GOLD[e["type"]] for d in docs for e in d["entities"] if e["type"] in GOLD)
    report = {
        "dataset": "woohyun212/k-pii-bench test_track_a @844f92f7",
        "sample_size": len(docs),
        "seed": SEED,
        "gold_counts": dict(sorted(gold_counts.items())),
        "unscored_gold_types": sorted(
            {e["type"] for d in docs for e in d["entities"]} - set(GOLD)
        ),
        "setups": {},
    }
    names = sys.argv[4].split(",") if len(sys.argv) > 4 else list(SETUPS)
    for name in names:
        for threshold in (0.0,):
            t0 = time.perf_counter()
            report["setups"][f"{name}@{threshold}"] = run(name, docs, threshold)
            print(f"{name}@{threshold} done in {time.perf_counter() - t0:.1f}s", flush=True)
    json.dump(report, open(out_path, "w"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
