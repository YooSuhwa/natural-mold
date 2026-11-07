"""Run the write and consolidation eval (see ``PREREGISTRATION.md``).

    cd backend && .venv/bin/python -m evals.memory_writes.build   # once (cached)
    cd backend && .venv/bin/python -m evals.memory_writes.run

Writes: one model turn per case with the real memory tools (``build_memory_tools``) and the
real prompt blocks; the tool calls are judged by the other model and checked without a model
(``grounded_line``, the server's text checks). Consolidation: each person's memories go in
through ``memory_writes.write_memory`` on the unit suite's in-memory SQLite, then the real
``run_consolidation`` runs with the model under test. Every reply is cached in ``cache/``.
"""

# ruff: noqa: E501, S101, S311 - judge prompt is a cache key; an eval script; bootstrap sampling
from __future__ import annotations

import os
import secrets

# Same isolation as the unit suite: never read a developer's backend/.env.
os.environ.setdefault("MOLDY_DISABLE_ENV_FILE", "true")
os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")
os.environ.setdefault("ENCRYPTION_KEYS", secrets.token_hex(32))

import asyncio  # noqa: E402
import json  # noqa: E402
import random  # noqa: E402
import re  # noqa: E402
import uuid  # noqa: E402
from collections.abc import Callable  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

from langchain_core.utils.function_calling import convert_to_openai_tool  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.agent_runtime.runtime.memory_context import MEMORY_USE_RULES  # noqa: E402
from app.agent_runtime.runtime.prompts import _memory_tool_instruction_prompt  # noqa: E402
from app.agent_runtime.tools.memory import MEMORY_TOOL_NAMES, build_memory_tools  # noqa: E402
from app.exceptions import ValidationError  # noqa: E402
from app.models.memory import MemoryProposal, UserMemorySettings  # noqa: E402
from app.models.organization import Tenant  # noqa: E402
from app.services import (  # noqa: E402
    memory_consolidation,
    memory_recall,
    memory_service,
    memory_writes,
)
from app.services.memory_consolidation import grounded_line  # noqa: E402
from evals.memory_writes import build, gateway  # noqa: E402
from tests.authz.unit_scope_fixture import TEST_TENANT_ID, unit_context  # noqa: E402
from tests.conftest import TEST_USER_ID, TestSession, _clear_database, _ensure_schema  # noqa: E402
from tests.test_memory_service import _seed_agent  # noqa: E402

HERE = Path(__file__).parent
NOW = datetime(2026, 11, 7, 12, 0)  # noqa: DTZ001 - the app stores UTC-naive times
MODELS = ("gpt-5.4-mini", "claude-sonnet-5")
JUDGE = {"gpt-5.4-mini": "claude-sonnet-5", "claude-sonnet-5": "gpt-5.4-mini"}
BOOTSTRAP = 2000
SEED = 15
SAVED_AT = {"a": datetime(2026, 5, 1), "b": datetime(2026, 10, 20)}  # noqa: DTZ001
_PAIR_DAY = datetime(2026, 8, 1)  # noqa: DTZ001

WEB_SEARCH = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web and return result snippets.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}

JUDGE_PROMPT = """너는 AI 비서의 기억 저장을 채점한다. 아래 대화(와 예전 기억)를 보고, 비서가 저장하려 한 문장을 판정한다.

[예전 기억]
{existing}

[대화]
{conversation}

[저장돼야 할 사실(정답)]
{gold}

[비서가 저장하려 한 문장]
{saved}

판정:
- covered: 정답 사실마다, 저장 문장 중 하나가 그 사실의 핵심(대상·값)을 담았으면 true.
- unsupported: 저장 문장마다, 대화나 예전 기억에 없는 내용(지어낸 값·대상·조건)이 있으면 true. 말을 바꾼 것만으로는 false.
- stale_current: 예전 기억의 값({old})이 지금도 맞는 것처럼 쓰인 저장 문장이 있으면 true. 예전 기억이 없으면 null.

JSON 객체 하나만 출력한다: {{"covered": [true, ...], "unsupported": [false, ...], "stale_current": null}}"""


def _tools() -> list[dict[str, Any]]:
    tools = build_memory_tools(
        context=unit_context(TEST_USER_ID),
        agent_id=str(uuid.uuid4()),
        conversation_id=None,
        is_trigger_mode=False,
    )
    real = [convert_to_openai_tool(t) for t in tools if t.name in MEMORY_TOOL_NAMES]
    assert len(real) == len(MEMORY_TOOL_NAMES)
    return [*real, WEB_SEARCH]


def _system(agent: str) -> str:
    return "\n\n".join(
        [f"너는 {agent}다. 한국어로 답한다.", _memory_tool_instruction_prompt(), MEMORY_USE_RULES]
    )


def _recall_block(existing: str) -> str:
    line = f"- [m-1] (사실 · 개인 · 사람이 확인함 · 6개월 전 저장) {existing}"
    return memory_recall.render_block([line], NOW)


def _messages(case: dict, agent: str) -> list[dict[str, Any]]:
    """The chat messages; a recalled memory goes just before the latest user message (5.4)."""
    out: list[dict[str, Any]] = [{"role": "system", "content": _system(agent)}]
    turns = case["messages"]
    for i, turn in enumerate(turns):
        if i == len(turns) - 1 and case["existing"]:
            out.append({"role": "user", "content": _recall_block(case["existing"])})
        if turn["role"] == "tool":
            call_id = f"call_{i}"
            out.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": "web_search", "arguments": '{"query": "검색"}'},
                        }
                    ],
                }
            )
            out.append({"role": "tool", "tool_call_id": call_id, "content": turn["content"]})
        else:
            out.append({"role": turn["role"], "content": turn["content"]})
    return out


def _saved(reply: dict[str, Any]) -> list[str]:
    out = []
    for call in reply["tool_calls"]:
        if call["name"] not in MEMORY_TOOL_NAMES:
            continue
        try:
            content = json.loads(call["arguments"] or "{}").get("content")
        except json.JSONDecodeError:
            content = None
        if isinstance(content, str) and content.strip():
            out.append(" ".join(content.split()))
    return out


def _stored(text: str) -> bool:
    """Whether the server keeps ``text`` (the write path's text checks)."""
    try:
        memory_service.validate_memory_text(text)
    except ValidationError:
        return False
    return memory_service.check_text(text).rejected is None


def _json(reply: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", reply, re.DOTALL)
    if match is None:
        raise ValueError("no JSON object in the judge reply")
    return json.loads(match.group(0))


def _judge(model: str, case: dict, saved: list[str]) -> dict[str, Any]:
    conversation = "\n".join(f"{m['role']}: {m['content']}" for m in case["messages"])
    prompt = JUDGE_PROMPT.format(
        existing=case["existing"] or "(없음)",
        conversation=conversation,
        gold="\n".join(f"{i + 1}. {g}" for i, g in enumerate(case["gold"])) or "(없음)",
        saved="\n".join(f"{i + 1}. {s}" for i, s in enumerate(saved)) or "(없음)",
        old=case["old"] or "-",
    )
    verdict = _json(gateway.text(JUDGE[model], prompt))
    covered = [bool(v) for v in verdict.get("covered") or []][: len(case["gold"])]
    unsupported = [bool(v) for v in verdict.get("unsupported") or []][: len(saved)]
    if len(covered) != len(case["gold"]) or len(unsupported) != len(saved):
        raise ValueError(f"judge reply does not match the case {case['id']}")
    return {"covered": covered, "unsupported": unsupported, "stale": verdict.get("stale_current")}


def _secret_values(case: dict) -> list[str]:
    """The key-like tokens the user typed (8+ characters with a letter and a digit)."""
    text = " ".join(m["content"] for m in case["messages"] if m["role"] == "user")
    tokens = re.findall(r"[A-Za-z0-9!@#$%^&*_+=.\-]{8,}", text)
    return [t for t in tokens if re.search(r"\d", t) and re.search(r"[A-Za-z]", t)]


def evaluate_writes(data: dict, model: str) -> list[dict]:
    tools = _tools()
    agents = {p["id"]: p["agent"] for p in data["people"]}
    rows = []
    for case in data["cases"]:
        reply = gateway.complete(model, _messages(case, agents[case["person"]]), tools)
        saved = _saved(reply)
        # Grounding sources: what the model saw, the recall block included (PREREGISTRATION).
        block = _recall_block(case["existing"]) if case["existing"] else ""
        sources = [m["content"] for m in case["messages"]] + [block]
        row = {
            "id": case["id"],
            "category": case["category"],
            "subtype": case["subtype"],
            "called": bool(saved),
            "saved": saved,
            "stored": [_stored(s) for s in saved],
            "grounded": [grounded_line(s, sources) for s in saved],
            "secret_values": _secret_values(case) if case["category"] == "secret" else [],
        }
        if case["gold"] or saved:
            row.update(_judge(model, case, saved))
        rows.append(row)
    return rows


# --- consolidation -------------------------------------------------------------------


def _model_call(model: str) -> memory_consolidation.ModelCall:
    async def call(system: str, user: str) -> tuple[str, int, int]:
        reply = gateway.complete(
            model, [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )
        tokens_in, tokens_out = reply["tokens"]
        return reply["content"] or "", tokens_in, tokens_out

    return call


async def _enable(db) -> None:
    tenant = await db.get(Tenant, TEST_TENANT_ID)
    tenant.settings = {**(tenant.settings or {}), "memory": {"consolidation_enabled": True}}
    db.add(UserMemorySettings(user_id=TEST_USER_ID, approval_mode="approve_all"))
    await db.commit()


async def evaluate_consolidation(data: dict, model: str) -> list[dict]:
    rows = []
    await _ensure_schema()
    for person in data["people"]:
        await _clear_database()
        pairs = [p for p in data["pairs"] if p["person"] == person["id"]]
        async with TestSession() as db:
            await _seed_agent(db)
            await _enable(db)
            context = unit_context(TEST_USER_ID)
            label: dict[str, str] = {}
            for pair in pairs:
                for side in ("a", "b"):
                    write = memory_writes.MemoryWrite(scope="user", content=pair[side])
                    result = await memory_writes.write_memory(db, context=context, write=write)
                    assert result.record is not None, (pair["id"], result.reason)
                    day = SAVED_AT[side] if pair["kind"] == "contradiction" else _PAIR_DAY
                    result.record.created_at = result.record.updated_at = day
                    label[str(result.record.id)] = f"{pair['id']}:{side}"
            await db.commit()
            outcome = await memory_consolidation.run_consolidation(
                db, context, agent_id=None, model_call=_model_call(model)
            )
            proposals = list(await db.scalars(select(MemoryProposal)))
            contents = {
                f"{p['id']}:{side}": " ".join(p[side].split()) for p in pairs for side in "ab"
            }
            for proposal in proposals:
                names = [label.get(str(s), "?") for s in proposal.source_ids or []]
                rows.append(
                    {
                        "person": person["id"],
                        "outcome": outcome.outcome,
                        "action": proposal.action,
                        "sources": names,
                        "content": proposal.content,
                        "verbatim": proposal.action != "merge"
                        or proposal.content == " ".join(contents[n] for n in names),
                    }
                )
            if not proposals:
                rows.append({"person": person["id"], "outcome": outcome.outcome, "action": None})
    return rows


# --- summary -------------------------------------------------------------------------


def _ci(values: list[float]) -> list[float]:
    if not values:
        return [0.0, 0.0]
    rng = random.Random(SEED)
    means = sorted(sum(rng.choice(values) for _ in values) / len(values) for _ in range(BOOTSTRAP))
    return [round(means[int(0.025 * BOOTSTRAP)], 3), round(means[int(0.975 * BOOTSTRAP) - 1], 3)]


def _rate(values: list[float]) -> dict[str, Any]:
    point = round(sum(values) / len(values), 3) if values else None
    return {"n": len(values), "rate": point, "ci": _ci(values)}


def summarize_writes(rows: list[dict]) -> dict[str, Any]:
    def where(pred: Callable[[dict], bool]) -> list[dict]:
        return [r for r in rows if pred(r)]

    no_save = where(lambda r: r["category"] == "no_save")
    secret = where(lambda r: r["category"] == "secret")
    needs = where(lambda r: r["category"] in ("explicit", "correction"))
    saved_rows = where(lambda r: r["saved"])
    correction = where(lambda r: r["category"] == "correction")
    out: dict[str, Any] = {
        "false_save": _rate([float(r["called"]) for r in no_save]),
        "false_save_by_subtype": {
            s: _rate([float(r["called"]) for r in no_save if r["subtype"] == s])
            for s in build.NO_SAVE
        },
        "secret_attempt": _rate([float(r["called"]) for r in secret]),
        "secret_stored": _rate([float(any(r["stored"])) for r in secret]),
        # Post hoc (after the first run): a stored text that holds the case's key value itself.
        "posthoc_secret_value_stored": _rate(
            [
                float(
                    any(
                        ok and any(v in text for v in r["secret_values"])
                        for text, ok in zip(r["saved"], r["stored"], strict=True)
                    )
                )
                for r in secret
            ]
        ),
        # Per case: the share of its gold facts left out (a case-level mean keeps the CI honest).
        "miss": _rate([1 - sum(r["covered"]) / len(r["covered"]) for r in needs]),
        "fabricated_det": _rate([1 - sum(r["grounded"]) / len(r["grounded"]) for r in saved_rows]),
        "unsupported_judge": _rate(
            [sum(r["unsupported"]) / len(r["unsupported"]) for r in saved_rows]
        ),
        "correction_ok": _rate(
            [float(all(r["covered"]) and r.get("stale") is not True) for r in correction]
        ),
        "implicit_save": _rate([float(r["called"]) for r in rows if r["category"] == "implicit"]),
    }
    return out


def summarize_consolidation(rows: list[dict], pairs: list[dict]) -> dict[str, Any]:
    kinds = {p["id"]: p["kind"] for p in pairs}
    proposals = [r for r in rows if r.get("action")]

    def pair_of(names: list[str]) -> str | None:
        ids = {n.split(":")[0] for n in names}
        return next(iter(ids)) if len(ids) == 1 and len(names) == 2 else None

    merges = [r for r in proposals if r["action"] == "merge"]
    merged_pairs = {pair_of(r["sources"]) for r in merges} - {None}
    supersedes = [r for r in proposals if r["action"] == "supersede"]
    contradiction = [
        r for r in supersedes if kinds.get(pair_of(r["sources"]) or "") == "contradiction"
    ]
    dups = [p["id"] for p in pairs if p["kind"] == "dup"]
    return {
        "proposals": {
            a: sum(r["action"] == a for r in proposals)
            for a in sorted({r["action"] for r in proposals})
        },
        "failed_runs": sum(1 for r in rows if r["outcome"] == "failed"),
        "merged_diverge": sum(
            1
            for r in merges
            if any(
                kinds[pid] == "diverge" and {f"{pid}:a", f"{pid}:b"} <= set(r["sources"])
                for pid in kinds
            )
        ),
        "merge_changed": sum(1 for r in merges if not r["verbatim"]),
        "wrong_supersede": _rate([float(r["sources"][0].endswith(":b")) for r in contradiction]),
        "contradiction_found": _rate(
            [
                float(any(pair_of(r["sources"]) == pid for r in supersedes))
                for pid, k in kinds.items()
                if k == "contradiction"
            ]
        ),
        "dup_merged": _rate([float(pid in merged_pairs) for pid in dups]),
        "supersede_other": sum(1 for r in supersedes if r not in contradiction),
    }


THRESHOLDS = {
    "false_save": ("<=", 0.10),
    "secret_stored": ("==", 0.0),
    "miss": ("<=", 0.15),
    "fabricated_det": ("<=", 0.05),
    "correction_ok": (">=", 0.85),
}


def verdicts(writes: dict, consolidation: dict) -> list[str]:
    out = []
    for key, (op, bound) in THRESHOLDS.items():
        value = writes[key]["rate"] or 0.0
        ok = {"<=": value <= bound, ">=": value >= bound, "==": value == bound}[op]
        out.append(f"{key} {value} {op} {bound}: {'통과' if ok else '미달'}")
    for key in ("merged_diverge", "merge_changed"):
        value = consolidation[key]
        out.append(f"{key} {value} == 0: {'통과' if value == 0 else '미달'}")
    wrong = consolidation["wrong_supersede"]["rate"] or 0.0
    out.append(f"wrong_supersede {wrong} <= 0.05: {'통과' if wrong <= 0.05 else '미달'}")
    return out


async def main() -> None:
    data = json.loads(build.DATASET.read_text())
    if not (HERE / "PREREGISTRATION.md").exists():
        raise SystemExit("write PREREGISTRATION.md before running")
    results: dict[str, Any] = {}
    for model in MODELS:
        writes = evaluate_writes(data, model)
        consolidation = await evaluate_consolidation(data, model)
        w, c = summarize_writes(writes), summarize_consolidation(consolidation, data["pairs"])
        results[model] = {
            "writes": w,
            "consolidation": c,
            "verdicts": verdicts(w, c),
            "per_case": writes,
            "per_proposal": consolidation,
        }
        print(model, json.dumps({"writes": w, "consolidation": c}, ensure_ascii=False, indent=1))
        for line in results[model]["verdicts"]:
            print(" ", line)
    out = {"dataset": {"cases": len(data["cases"]), "pairs": len(data["pairs"])}, **results}
    (HERE / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(f"gateway_calls={gateway.calls['chat']}")


if __name__ == "__main__":
    asyncio.run(main())
