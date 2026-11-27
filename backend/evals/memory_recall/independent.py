"""Evaluate F09 on synthetic, independent Korean questions. Run with --help from backend/."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import NormalDist
from urllib.parse import urlsplit

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

# Keep the standalone evaluator independent of a developer's .env, as in the old eval.
# Import its test DB setup before any app module so settings load in the same isolated mode.
from evals.memory_recall import run
from evals.memory_recall.independent_corpus import validate_corpus
from evals.memory_recall.run import _day

# isort: split
from app.database import platform_session
from app.models.memory import MemoryEmbedding, MemoryRecord
from app.models.organization import Tenant
from app.services import memory_embedder, memory_embedding_access, memory_recall, memory_vectors
from app.text import korean_terms
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID, TestSession, _clear_database, _ensure_schema
from tests.test_memory_service import _seed_agent

CORPUS = run.HERE / "independent_ko.json"
SCHEMA = "moldy.memory-recall.quality.v1"


@dataclass(frozen=True)
class Candidate:
    tenant_id: str
    org_id: str
    model_id: str
    provider: str
    base_url: str
    model: str
    dimensions: int
    cosine_floor: float


async def approved_candidate(
    db: AsyncSession, tenant_id: uuid.UUID, org_id: uuid.UUID, model_id: uuid.UUID, floor: float
) -> tuple[Candidate, memory_embedder.OpenAIShapedEmbedder]:
    if not math.isfinite(floor) or not 0 < floor <= 1:
        raise ValueError("cosine floor must be greater than 0 and at most 1")
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None or memory_vectors.chosen_model_id(tenant.settings) != model_id:
        raise ValueError("this tenant has not selected the requested embedding model")
    embedder = await memory_embedding_access.org_embedder_for(
        db, tenant_id, org_id, model_id, generation=memory_vectors.generation(tenant.settings)
    )
    if embedder is None or embedder.registry_id != model_id:
        raise ValueError("the selected model is unavailable for this organization")
    url = urlsplit(embedder.base_url)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
    ):
        raise ValueError("the approved base URL must not contain credentials, query or fragment")
    if embedder.dimensions <= 0:
        raise ValueError("the approved model has no embedding dimensions")
    return Candidate(
        str(tenant_id),
        str(org_id),
        str(model_id),
        embedder.provider,
        embedder.base_url.rstrip("/"),
        embedder.model,
        embedder.dimensions,
        floor,
    ), embedder


async def _current_provider(candidate: Candidate) -> memory_embedder.OpenAIShapedEmbedder:
    # A run issues many requests. Recheck the catalog, credential destination and selected
    # model before embedding each persona and each question, including mid-run revocation.
    async with platform_session("memory.independent_evaluation.model") as db:
        actual, provider = await approved_candidate(
            db,
            uuid.UUID(candidate.tenant_id),
            uuid.UUID(candidate.org_id),
            uuid.UUID(candidate.model_id),
            candidate.cosine_floor,
        )
    if actual != candidate:
        raise ValueError("candidate registration changed during evaluation")
    return provider


async def _vectors(
    embedder: memory_embedder.MemoryEmbedder, texts: list[str], seconds: float
) -> list[np.ndarray]:
    rows = await embedder.embed(texts, seconds=seconds)
    if len(rows) != len(texts) or any(
        not memory_embedder.valid_vector(row, embedder.dimensions) for row in rows
    ):
        raise ValueError("provider returned missing, malformed or non-unit embeddings")
    return rows


async def _seed_snapshot(
    db: AsyncSession,
    memories: list[dict],
    embedder: memory_embedder.MemoryEmbedder | None,
    now: datetime,
) -> tuple[uuid.UUID, dict[uuid.UUID, str]]:
    agent_id, _ = await _seed_agent(db)
    ids: dict[uuid.UUID, str] = {}
    # Materialize already-approved synthetic ledger rows. Stable IDs resolve score ties;
    # the fixed corpus clock avoids changes in recency between the two modes or reruns.
    for m in memories:
        rid = uuid.uuid5(uuid.NAMESPACE_URL, f"moldy:independent-recall:{m['id']}")
        ids[rid] = m["id"]
        db.add(
            MemoryRecord(
                id=rid,
                tenant_id=TEST_TENANT_ID,
                org_id=TEST_ORG_ID,
                user_id=TEST_USER_ID,
                agent_id=agent_id if m["scope"] == "agent" else None,
                scope=m["scope"],
                store_path=f"/memories/{m['scope']}",
                origin="user_stated",
                content=m["content"],
                kind=m["kind"],
                importance=m["importance"],
                created_at=now,
                updated_at=now,
                occurred_start=_day(m["occurred_start"]),
                occurred_end=_day(m["occurred_end"]),
                approved_by=TEST_USER_ID,
                approved_at=now,
                approval_channel="direct",
                status="active",
                search_tokens=" ".join(korean_terms.fallback_terms(m["content"])),
                search_tokens_analyzer=korean_terms.FALLBACK_ANALYZER,
            )
        )
    await db.flush()
    if embedder is not None:
        model_key = memory_embedder.vector_model_key(embedder)
        for start in range(0, len(memories), 32):
            batch = memories[start : start + 32]
            texts = [m["content"] for m in batch]
            rows = await _vectors(embedder, texts, seconds=30.0)
            for m, vector in zip(batch, rows, strict=True):
                rid = uuid.uuid5(uuid.NAMESPACE_URL, f"moldy:independent-recall:{m['id']}")
                db.add(
                    MemoryEmbedding(
                        tenant_id=TEST_TENANT_ID,
                        org_id=TEST_ORG_ID,
                        user_id=TEST_USER_ID,
                        record_id=rid,
                        model=model_key,
                        dimensions=embedder.dimensions,
                        text_hash=memory_embedder.text_hash(m["content"]),
                        vector=memory_embedder.to_bytes(vector),
                    )
                )
    await db.commit()
    return agent_id, ids


async def _measure(
    db: AsyncSession,
    question: dict,
    agent_id: uuid.UUID,
    ids: dict[uuid.UUID, str],
    now: datetime,
    embedder: memory_embedder.MemoryEmbedder | None,
    floor: float | None,
) -> dict:
    if memory_recall.short_message_rule(question["question"]) is not None:
        raise ValueError(f"{question['id']} uses skip/reuse rather than fresh recall")
    messages = [question["question"]]
    if question["previous"]:
        messages.append(question["previous"])
    query = memory_recall.Query.of(messages)
    started = time.perf_counter()
    if embedder is not None and floor is None:
        raise ValueError("hybrid evaluation requires a preregistered cosine floor")
    # Let rank perform each uncached embedding under the production timeout. A degraded
    # keyword result cannot count toward the candidate's quality or latency.
    ranked = await memory_recall.rank(
        db,
        unit_context(TEST_USER_ID),
        query,
        memory_recall.Filters(scopes="both", agent_id=agent_id),
        now=now,
        embedder=embedder,
        candidate_floor=floor,
    )
    if ranked.fallback is not None:
        raise RuntimeError(f"unexpected recall fallback: {ranked.fallback}")
    selected = memory_recall.select_within_budget(ranked.items, now)
    block = memory_recall.render_block(selected.lines, now)
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "inserted": [ids[item.record.id] for item in selected.items],
        "tokens": memory_recall.estimate_tokens(block),
        "ms": elapsed_ms,
    }


async def collect(data: dict, candidate: Candidate | None) -> dict[str, dict]:
    results: dict[str, dict] = {"keyword": {}}
    if candidate is not None:
        results["hybrid"] = {}
    now = datetime.fromisoformat(data["today"] + "T12:00:00")
    await _ensure_schema()
    for person in data["people"]:
        await _clear_database()  # tests.sqlite_test_engine is per-process, in-memory SQLite.
        mine = [m for m in data["memories"] if m["person"] == person["id"]]
        async with TestSession() as db:
            provider = await _current_provider(candidate) if candidate else None
            agent_id, ids = await _seed_snapshot(db, mine, provider, now)
            for q in data["questions"]:
                if q["person"] != person["id"]:
                    continue
                results["keyword"][q["id"]] = await _measure(db, q, agent_id, ids, now, None, None)
                if candidate is not None:
                    results["hybrid"][q["id"]] = await _measure(
                        db,
                        q,
                        agent_id,
                        ids,
                        now,
                        await _current_provider(candidate),
                        candidate.cosine_floor,
                    )
    return results


def _primary(question: dict, observation: dict) -> float:
    gold = set(question["gold"])
    if not gold:
        return float(not observation["inserted"])
    return len(gold.intersection(observation["inserted"][:10])) / len(gold)


def _estimate(
    questions: Sequence[dict],
    values: Sequence[float],
    statistic: Callable[[np.ndarray], float],
    *,
    binary: bool = False,
) -> dict:
    array = np.array(values, dtype=float)
    groups: dict[str, list[int]] = {}
    for i, question in enumerate(questions):
        groups.setdefault(question["group"], []).append(i)
    if len(array) != len(questions) or not groups:
        raise ValueError("each metric needs one observation per question")
    point = statistic(array)
    if binary and len(groups) == len(array) and all(v in (0.0, 1.0) for v in values):
        # Single independent Bernoulli targets, including all-success/all-failure results.
        z = NormalDist().inv_cdf(0.975)
        n = len(array)
        denom = 1 + z * z / n
        center = (point + z * z / (2 * n)) / denom
        radius = z * math.sqrt(point * (1 - point) / n + z * z / (4 * n * n)) / denom
        ci = [0.0 if point == 0 else center - radius, 1.0 if point == 1 else center + radius]
    else:
        rng = np.random.default_rng(run.build.SEED)
        members = list(groups.values())
        picks = rng.integers(len(members), size=(run.BOOTSTRAP, len(members)))
        if len(groups) == len(array):
            samples = [statistic(array[index]) for index in picks]
        else:
            samples = [
                statistic(array[np.concatenate([members[i] for i in index])]) for index in picks
            ]
        ci = np.quantile(samples, [0.025, 0.975]).tolist()
    return {"n": len(values), "value": point, "ci95": ci}


def _mean(values: np.ndarray) -> float:
    return float(np.mean(values))


def _p95(values: np.ndarray) -> float:
    return float(np.quantile(values, 0.95, method="inverted_cdf"))


def summarize(questions: list[dict], results: dict[str, dict]) -> dict:
    modes = {}
    for mode, observations in results.items():
        categories = {}
        for category in run.build.CATEGORIES:
            subset = [q for q in questions if q["category"] == category]
            values = [_primary(q, observations[q["id"]]) for q in subset]
            metric = "irrelevant_rejection" if category == "unrelated" else "recall_at_10"
            categories[category] = {
                "metric": metric,
                **_estimate(subset, values, _mean, binary=all(len(q["gold"]) <= 1 for q in subset)),
            }
        rows = [observations[q["id"]] for q in questions]
        modes[mode] = {
            "categories": categories,
            "mean_prompt_tokens": _estimate(questions, [row["tokens"] for row in rows], _mean),
            "p95_elapsed_ms": _estimate(questions, [row["ms"] for row in rows], _p95),
        }
    return modes


def verdict(modes: dict) -> dict:
    if "hybrid" not in modes:
        return {"passed": False, "reason": "candidate_not_evaluated"}
    hybrid = modes["hybrid"]["categories"]
    if hybrid["unrelated"]["value"] < 0.90:
        return {"passed": False, "reason": "irrelevant_rejection_below_0.90"}
    if hybrid["paraphrase"]["value"] <= modes["keyword"]["categories"]["paraphrase"]["value"]:
        return {"passed": False, "reason": "no_paraphrase_improvement"}
    return {"passed": True, "reason": "passed"}


async def evaluate(raw: bytes, historical: dict, args: argparse.Namespace) -> dict:
    data = json.loads(raw)
    validate_corpus(data, historical)
    candidate = None
    if args.approved_model_id is not None:
        # The only non-fixture DB read is the operator-requested tenant/model configuration.
        if not os.environ.get("DATABASE_URL"):
            raise ValueError("supply DATABASE_URL for the approved model registry")
        async with platform_session("memory.independent_evaluation.model") as db:
            candidate, _ = await approved_candidate(
                db, args.tenant_id, args.org_id, args.approved_model_id, args.cosine_floor
            )
    results = await collect(data, candidate)
    modes = summarize(data["questions"], results)
    delta = None
    if candidate is not None:
        delta = _estimate(
            data["questions"],
            [
                results["hybrid"][q["id"]]["tokens"] - results["keyword"][q["id"]]["tokens"]
                for q in data["questions"]
            ],
            _mean,
        )
    return {
        "schema": SCHEMA,
        "corpus_sha256": hashlib.sha256(raw).hexdigest(),
        "candidate": asdict(candidate) if candidate else None,
        "keyword_floor": memory_recall.KEYWORD_FLOOR,
        "analyzer": korean_terms.FALLBACK_ANALYZER,
        "modes": modes,
        "mean_added_prompt_tokens_vs_keyword": delta,
        "gate": verdict(modes),
        "per_question": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new report file")
    parser.add_argument("--tenant-id", type=uuid.UUID)
    parser.add_argument("--org-id", type=uuid.UUID)
    parser.add_argument("--approved-model-id", type=uuid.UUID)
    parser.add_argument("--cosine-floor", type=float, help="fixed before inspecting this report")
    args = parser.parse_args()
    selector = (args.tenant_id, args.org_id, args.approved_model_id, args.cosine_floor)
    if any(value is not None for value in selector) and any(value is None for value in selector):
        parser.error("pass --tenant-id, --org-id, --approved-model-id and --cosine-floor together")
    if args.output.exists():
        parser.error("output already exists; choose a new file")
    report = asyncio.run(
        evaluate(CORPUS.read_bytes(), json.loads((run.HERE / "dataset.json").read_bytes()), args)
    )
    with args.output.open("x", encoding="utf-8") as output:
        output.write(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
