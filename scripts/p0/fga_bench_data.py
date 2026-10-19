"""Exact artificial tuple workload from the supplied A-12 benchmark."""

from __future__ import annotations

import random
from typing import Final, TypedDict


class TupleKey(TypedDict):
    user: str
    relation: str
    object: str


T: Final = 10
OPT: Final = 5
UPO: Final = 100
GPO: Final = 5
APO: Final = 400
CPO: Final = 100
orgs: Final = [f"t{t}o{o}" for t in range(T) for o in range(OPT)]
tenant_of: Final = {o: o.split("o")[0] for o in orgs}
users: Final = {o: [f"{o}u{i}" for i in range(UPO)] for o in orgs}
groups: Final = {o: [f"{o}g{g}" for g in range(GPO)] for o in orgs}
agents: Final = {o: [f"{o}a{i}" for i in range(APO)] for o in orgs}
creds: Final = {o: [f"{o}c{i}" for i in range(CPO)] for o in orgs}


def tk(u: str, r: str, o: str) -> TupleKey:
    return {"user": u, "relation": r, "object": o}


def tuples(v3: bool) -> tuple[list[TupleKey], list[tuple[str, str]]]:
    """Reproduce the supplied workload; seeds model load, never secrets."""
    rnd_load = random.Random(20261010)  # noqa: S311 - reproducible artificial workload
    out: list[TupleKey] = []
    for o in orgs:
        t = tenant_of[o]
        out.append(tk(f"tenant:{t}", "tenant", f"organization:{o}"))
        for i, u in enumerate(users[o]):
            out.append(tk(f"user:{u}", "member", f"tenant:{t}"))
            role = "admin" if i < 2 else "builder" if i < 10 else "member"
            out.append(tk(f"user:{u}", role, f"organization:{o}"))
        for gi, g in enumerate(groups[o]):
            out.append(tk(f"organization:{o}", "org", f"group:{g}"))
            for u in users[o][gi * 20 : (gi + 1) * 20]:
                out.append(tk(f"user:{u}", "member", f"group:{g}"))
        for a in agents[o]:
            out.append(tk(f"organization:{o}", "org", f"agent:{a}"))
            if v3:
                out.append(tk(f"agent:{a}", "resident_agent", f"organization:{o}"))
            out.append(tk(f"user:{rnd_load.choice(users[o])}", "owner", f"agent:{a}"))
            for u in rnd_load.sample(users[o], 2):
                out.append(tk(f"user:{u}", "viewer", f"agent:{a}"))
            if rnd_load.random() < 0.5:
                out.append(
                    tk(f"group:{rnd_load.choice(groups[o])}#member", "consumer", f"agent:{a}")
                )
            if rnd_load.random() < 0.2:
                out.append(tk(f"user:{rnd_load.choice(users[o])}", "editor", f"agent:{a}"))
            if rnd_load.random() < 0.1:
                out.append(tk(f"organization:{o}#member", "consumer", f"agent:{a}"))
        for c in creds[o]:
            out.append(tk(f"organization:{o}", "org", f"credential:{c}"))
            out.append(tk(f"user:{rnd_load.choice(users[o])}", "owner", f"credential:{c}"))
            for a in rnd_load.sample(agents[o], 2):
                out.append(tk(f"agent:{a}", "consumer", f"credential:{c}"))
    # 잘못 기록된 교차 회사 튜플 100건 (다른 회사 사용자에게 viewer)
    rnd = random.Random(7)  # noqa: S311 - reproduce supplied cross-tenant misgrants
    bad: list[tuple[str, str]] = []
    for _ in range(100):
        o1, o2 = rnd.sample(orgs, 2)
        while tenant_of[o1] == tenant_of[o2]:
            o1, o2 = rnd.sample(orgs, 2)
        bad.append((rnd.choice(users[o2]), rnd.choice(agents[o1])))
    out += [tk(f"user:{u}", "viewer", f"agent:{a}") for u, a in bad]
    return out, bad
