"""Run the recall eval: tune the floors on ``tune``, then report on ``report`` with CIs.

    cd backend && .venv/bin/python -m evals.memory_recall.build   # once (cached)
    cd backend && .venv/bin/python -m evals.memory_recall.run

The real ``memory_recall.rank`` and ``select_within_budget`` run on the unit suite's in-memory
SQLite; each person's memories go in through ``memory_writes.write_memory``. Vectors come from
the build's cache, so this makes no network call. The floors are monkeypatched per config.
"""

from __future__ import annotations

import os
import secrets

# Same isolation as the unit suite: never read a developer's backend/.env (OpenSearch etc.).
os.environ.setdefault("MOLDY_DISABLE_ENV_FILE", "true")
os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")
os.environ.setdefault("ENCRYPTION_KEYS", secrets.token_hex(32))

import asyncio  # noqa: E402
import dataclasses  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import statistics  # noqa: E402
import time  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

import numpy as np  # noqa: E402

from app.models.memory import MemoryEmbedding  # noqa: E402
from app.services import memory_embedder, memory_recall, memory_writes  # noqa: E402
from app.services.memory_recall import Filters, Query, Scored  # noqa: E402
from app.services.memory_service import MemoryShape  # noqa: E402
from evals.memory_recall import build, gateway  # noqa: E402
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context  # noqa: E402
from tests.conftest import TEST_USER_ID, TestSession, _clear_database, _ensure_schema  # noqa: E402
from tests.test_memory_service import _seed_agent  # noqa: E402

HERE = Path(__file__).parent
NOW = datetime(2026, 11, 7, 12, 0)  # noqa: DTZ001 - the app stores UTC-naive times
LARGE, SMALL = build.EMBEDDING_MODELS
KEYWORD_GRID = [round(0.15 + 0.05 * i, 2) for i in range(12)]
COSINE_GRID = [round(0.20 + 0.02 * i, 2) for i in range(26)]
BOOTSTRAP = 2000
ANSWERABLE = [c for c in build.CATEGORIES if c != "unrelated"]


@dataclasses.dataclass(frozen=True)
class Config:
    name: str
    model: str | None = None  # embedding model; None = keyword-only install
    keyword_floor: float = memory_recall.KEYWORD_FLOOR
    cosine_floor: float | None = None
    vector_only: bool = False
    rerank_cosine: bool = False  # 0.7·RRF + 0.3·cosine as the search score (GBrain)


class CachedEmbedder:
    """A ``MemoryEmbedder`` that serves the build's cached vectors."""

    dimensions = gateway.DIMENSIONS

    def __init__(self, vectors: gateway.Vectors) -> None:
        self.model = vectors.model
        self.vectors = vectors

    async def embed(self, texts: list[str], *, seconds: float) -> list[np.ndarray]:
        return [self.vectors.get(text) for text in texts]


def _day(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


async def _seed(db, memories: list[dict], embedders: dict[str, CachedEmbedder]):
    """One person's memories through the real write path, plus a vector per model."""
    agent_id, _ = await _seed_agent(db)
    context = unit_context(TEST_USER_ID)
    ids, skipped = {}, []
    for m in memories:
        shape = MemoryShape(
            kind=m["kind"],
            occurred_start=_day(m["occurred_start"]),
            occurred_end=_day(m["occurred_end"]),
        )
        write = memory_writes.MemoryWrite(
            scope=m["scope"],
            content=m["content"],
            agent_id=agent_id if m["scope"] == "agent" else None,
            shape=shape,
        )
        result = await memory_writes.write_memory(db, context=context, write=write)
        if result.record is None:
            skipped.append({"id": m["id"], "kind": result.kind, "reason": result.reason})
            continue
        result.record.importance = m["importance"]
        ids[result.record.id] = m["id"]
        for embedder in embedders.values():
            db.add(
                MemoryEmbedding(
                    org_id=TEST_ORG_ID,
                    tenant_id=TEST_TENANT_ID,
                    record_id=result.record.id,
                    user_id=TEST_USER_ID,
                    model=embedder.model,
                    dimensions=embedder.dimensions,
                    text_hash=memory_embedder.text_hash(m["content"]),
                    vector=memory_embedder.to_bytes(embedder.vectors.get(m["content"])),
                )
            )
    await db.commit()
    return agent_id, ids, skipped


def _with_cosine(items: list[Scored]) -> list[Scored]:
    """Re-rank with ``search = 0.7·RRF + 0.3·cosine``; recency and importance stay."""
    weight = memory_recall.WEIGHTS[0]
    out = []
    for item in items:
        search = 0.7 * item.search + 0.3 * (item.cosine or 0.0)
        final = item.final + weight * (search - item.search)
        out.append(dataclasses.replace(item, search=search, final=final))
    return sorted(out, key=lambda item: (-item.final, str(item.record.id)))


async def _recall(db, agent_id, ids, question: dict, config: Config, embedders) -> dict:
    messages = [question["question"]] + ([question["previous"]] if question["previous"] else [])
    embedder = embedders.get(config.model)
    floors = {f"{config.model}@{gateway.DIMENSIONS}": config.cosine_floor} if embedder else {}
    keyword_floor = math.inf if config.vector_only else config.keyword_floor
    with (
        patch.object(memory_recall, "KEYWORD_FLOOR", keyword_floor),
        patch.object(memory_recall, "VECTOR_FLOORS", floors),
    ):
        started = time.perf_counter()
        ranked = await memory_recall.rank(
            db,
            unit_context(TEST_USER_ID),
            Query.of(messages),
            Filters(scopes="both", agent_id=agent_id),
            embedder=embedder,
            now=NOW,
        )
        items = _with_cosine(ranked.items) if config.rerank_cosine else ranked.items
        chosen = memory_recall.select_within_budget(items, NOW)
        ms = (time.perf_counter() - started) * 1000
    if ranked.fallback is not None:
        raise RuntimeError(f"recall fell back: {ranked.fallback}")
    return {"inserted": [ids[i.record.id] for i in chosen.items], "tokens": chosen.tokens, "ms": ms}


async def evaluate(data: dict, configs: list[Config], split: str) -> tuple[dict, list]:
    embedders = {m: CachedEmbedder(gateway.Vectors(m)) for m in build.EMBEDDING_MODELS}
    results: dict[str, dict[str, dict]] = {c.name: {} for c in configs}
    skipped_all = []
    await _ensure_schema()
    for person in data["people"]:
        await _clear_database()
        mine = [m for m in data["memories"] if m["person"] == person["id"]]
        async with TestSession() as db:
            agent_id, ids, skipped = await _seed(db, mine, embedders)
            skipped_all += skipped
            for q in data["questions"]:
                if q["person"] != person["id"] or q["split"] != split:
                    continue
                for c in configs:
                    results[c.name][q["id"]] = await _recall(db, agent_id, ids, q, c, embedders)
    return results, skipped_all


# --- metrics --------------------------------------------------------------------------


def score(question: dict, result: dict) -> dict:
    inserted, gold = result["inserted"][:10], set(question["gold"])
    if not gold:
        return {"nothing": float(not inserted), "tokens": result["tokens"]}
    hits = [m in gold for m in inserted]
    dcg = sum(1 / math.log2(i + 2) for i, hit in enumerate(hits) if hit)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(len(gold), 10)))
    return {
        "recall": sum(hits) / len(gold),
        "ndcg": dcg / ideal,
        "precision": sum(hits) / len(inserted) if inserted else None,
        "tokens": result["tokens"],
    }


def _mean(values: list) -> float | None:
    kept = [v for v in values if v is not None]
    return round(statistics.fmean(kept), 4) if kept else None


def summarize(questions: list[dict], results: dict[str, dict]) -> dict:
    by_category: dict[str, dict] = {}
    for category in build.CATEGORIES:
        rows = [score(q, results[q["id"]]) for q in questions if q["category"] == category]
        keys = ("nothing",) if category == "unrelated" else ("recall", "ndcg", "precision")
        by_category[category] = {k: _mean([r[k] for r in rows]) for k in keys}
        by_category[category] |= {"n": len(rows), "tokens": _mean([r["tokens"] for r in rows])}
    ms = sorted(r["ms"] for r in results.values())
    return {
        "categories": by_category,
        "false_fire_rate": round(1 - by_category["unrelated"]["nothing"], 4),
        "mean_tokens": _mean([r["tokens"] for r in results.values()]),
        "latency_ms": {
            "p50": round(statistics.median(ms), 2),
            "p95": round(ms[math.ceil(0.95 * len(ms)) - 1], 2),
        },
    }


def _primary(question: dict, result: dict) -> float:
    row = score(question, result)
    return row["nothing"] if question["category"] == "unrelated" else row["recall"]


def bootstrap(questions: list[dict], all_results: dict, reference: str) -> dict:
    """Per category: group-level bootstrap CI of each config's primary metric, and the
    paired difference to ``reference`` on the same resamples."""
    rng = np.random.default_rng(build.SEED)
    out: dict[str, dict] = {}
    for category in build.CATEGORIES:
        mine = [q for q in questions if q["category"] == category]
        groups = sorted({q["group"] for q in mine})
        members = [[q for q in mine if q["group"] == g] for g in groups]
        values = {
            name: [[_primary(q, results[q["id"]]) for q in group] for group in members]
            for name, results in all_results.items()
        }
        draws: dict[str, list[float]] = {name: [] for name in values}
        for _ in range(BOOTSTRAP):
            picks = rng.integers(0, len(groups), len(groups))
            for name, per_group in values.items():
                sample = [v for i in picks for v in per_group[i]]
                draws[name].append(statistics.fmean(sample))
        out[category] = {}
        for name in values:
            diffs = np.subtract(draws[name], draws[reference])
            out[category][name] = {
                "ci": [round(float(np.percentile(draws[name], p)), 4) for p in (2.5, 97.5)],
                "diff_vs_" + reference: [
                    round(float(np.percentile(diffs, p)), 4) for p in (2.5, 97.5)
                ],
            }
    return out


# --- tuning ---------------------------------------------------------------------------


def _objective(summary: dict) -> float | None:
    cats = summary["categories"]
    if cats["unrelated"]["nothing"] < 0.9:
        return None
    return cats["same_word"]["recall"] + cats["paraphrase"]["recall"]


def _pick(grid: dict[float, dict]) -> float:
    """Best objective with "nothing recalled" ≥ 0.9; ties go to the higher (safer) floor."""
    scored = [(obj, floor) for floor, s in grid.items() if (obj := _objective(s)) is not None]
    if not scored:
        raise SystemExit("no floor keeps the unrelated 'nothing recalled' rate at 0.9")
    return max(scored)[1]


async def tune(data: dict, tune_questions: list[dict]) -> dict:
    keyword = [Config(f"kw@{f}", keyword_floor=f) for f in KEYWORD_GRID]
    results, _ = await evaluate(data, keyword, "tune")
    kw_grid = {c.keyword_floor: summarize(tune_questions, results[c.name]) for c in keyword}
    kw = _pick(kw_grid)
    cosine = [
        Config(f"{m}@{f}", model=m, keyword_floor=kw, cosine_floor=f)
        for m in (LARGE, SMALL)
        for f in COSINE_GRID
    ]
    results, _ = await evaluate(data, cosine, "tune")
    floors = {}
    grids = {"keyword": {str(k): v for k, v in kw_grid.items()}}
    for model in (LARGE, SMALL):
        grid = {
            c.cosine_floor: summarize(tune_questions, results[c.name])
            for c in cosine
            if c.model == model and c.cosine_floor is not None
        }
        floors[model] = _pick(grid)
        grids[model] = {str(k): v for k, v in grid.items()}
    return {"keyword_floor": kw, "cosine_floors": floors, "grids": grids}


# --- verdicts -------------------------------------------------------------------------

CRITERIA = [
    ("embeddings", "rrf", "same_word", 0.85),
    ("embeddings", "rrf", "paraphrase", 0.7),
    ("embeddings", "rrf", "unrelated", 0.9),
    ("keyword_only", "keyword", "same_word", 0.85),
    ("keyword_only", "keyword", "unrelated", 0.9),
]


def verdicts(summaries: dict, cis: dict) -> list[dict]:
    out = []
    for install, config, category, threshold in CRITERIA:
        cats = summaries[config]["categories"][category]
        point = cats["nothing"] if category == "unrelated" else cats["recall"]
        low, high = cis[category][config]["ci"]
        verdict = (
            "pass" if point >= threshold else "fail" if high < threshold - 0.02 else "borderline"
        )
        out.append(
            {
                "install": install,
                "config": config,
                "category": category,
                "threshold": threshold,
                "point": point,
                "ci": [low, high],
                "verdict": verdict,
            }
        )
    return out


def _table(summaries: dict, cis: dict) -> str:
    lines = [
        "| 범주 | n | " + " | ".join(summaries) + " |",
        "|---|---|" + "---|" * len(summaries),
    ]
    for category in build.CATEGORIES:
        n = next(iter(summaries.values()))["categories"][category]["n"]
        cells = []
        for name, summary in summaries.items():
            cats = summary["categories"][category]
            point = cats["nothing"] if category == "unrelated" else cats["recall"]
            low, high = cis[category][name]["ci"]
            ndcg = "" if category == "unrelated" else f" / nDCG {cats['ndcg']:.2f}"
            cells.append(f"{point:.2f} [{low:.2f}, {high:.2f}]{ndcg}")
        lines.append(f"| {category} | {n} | " + " | ".join(cells) + " |")
    for label, key in (("false_fire_rate", "false_fire_rate"), ("평균 토큰", "mean_tokens")):
        lines.append(f"| {label} | | " + " | ".join(str(s[key]) for s in summaries.values()) + " |")
    lines.append(
        "| 지연 p50/p95 ms | | "
        + " | ".join(
            f"{s['latency_ms']['p50']}/{s['latency_ms']['p95']}" for s in summaries.values()
        )
        + " |"
    )
    return "\n".join(lines)


async def main() -> None:
    data = json.loads(build.DATASET.read_text())
    tune_questions = [q for q in data["questions"] if q["split"] == "tune"]
    report_questions = [q for q in data["questions"] if q["split"] == "report"]
    tuned = await tune(data, tune_questions)
    kw, cos = tuned["keyword_floor"], tuned["cosine_floors"]
    print(f"tuned: KEYWORD_FLOOR={kw}, cosine={cos}", flush=True)

    if not (HERE / "PREREGISTRATION.md").exists():
        raise SystemExit("write PREREGISTRATION.md before running the reporting half")
    configs = [
        Config("keyword", keyword_floor=kw),
        Config("vector", model=LARGE, cosine_floor=cos[LARGE], vector_only=True),
        Config("rrf", model=LARGE, keyword_floor=kw, cosine_floor=cos[LARGE]),
        Config(
            "rrf_cos", model=LARGE, keyword_floor=kw, cosine_floor=cos[LARGE], rerank_cosine=True
        ),
        Config("rrf_small", model=SMALL, keyword_floor=kw, cosine_floor=cos[SMALL]),
    ]
    # Post hoc, after seeing the tuning grids: labelled, never used for a verdict.
    configs += [
        Config("posthoc_keyword@0.3", keyword_floor=0.3),
        Config("posthoc_keyword@0.35", keyword_floor=0.35),
        Config("posthoc_rrf@0.42", model=LARGE, keyword_floor=kw, cosine_floor=0.42),
        Config("posthoc_rrf@0.44", model=LARGE, keyword_floor=kw, cosine_floor=0.44),
        Config("posthoc_rrf@0.42_kw0.3", model=LARGE, keyword_floor=0.3, cosine_floor=0.42),
    ]
    results, skipped = await evaluate(data, configs, "report")
    summaries = {name: summarize(report_questions, r) for name, r in results.items()}
    cis = bootstrap(report_questions, results, reference="rrf")
    checks = verdicts(summaries, cis)
    latency_file = build.LATENCY
    out = {
        "dataset": {
            "memories": len(data["memories"]),
            "questions": {
                "tune": len(tune_questions),
                "report": len(report_questions),
            },
            "skipped_writes": skipped,
        },
        "tuning": tuned,
        "report": {"summaries": summaries, "bootstrap": cis, "verdicts": checks},
        "query_embedding_latency": json.loads(latency_file.read_text())
        if latency_file.exists()
        else None,
        "per_question": results,
    }
    (HERE / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    for posthoc in (False, True):
        part = {k: v for k, v in summaries.items() if k.startswith("posthoc_") == posthoc}
        print(_table(part, cis), end="\n\n")
    for check in checks:
        print(check)


if __name__ == "__main__":
    asyncio.run(main())
