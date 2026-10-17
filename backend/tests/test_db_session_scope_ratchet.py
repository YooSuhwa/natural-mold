"""B-13 ratchet: no unscoped database session leaves ``app/database.py``.

The raw ``async_sessionmaker`` (and every alias a module binds to it) may only
be read as the ``maker=`` of a scoped factory, as a module-level patch-surface
alias, or in an identity comparison. Every platform-scoped session names a
reason pinned below to plan 8.8/8.9 (or an owning follow-up task), and direct
engine connections are limited to the advisory-lock leaders and the
migration-role CLI. Transaction settings are only ever transaction-local.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
DATABASE = APP / "database.py"
# ``company_session`` (H-07) picks tenant_session or platform_session for a skill's company.
SCOPED = frozenset(
    {
        "tenant_session",
        "tenant_sessions",
        "platform_session",
        "platform_sessions",
        "company_session",
    }
)
PLATFORM = frozenset({"platform_session", "platform_sessions"})

# reason -> plan anchor. 8.9 rows are platform by design; "interim" rows are
# platform today because the per-row tenant split belongs to the named task.
PLATFORM_REASONS: dict[str, str] = {
    "seed.startup": "8.9 start-up data: platform data only at boot",
    "scheduler.trigger_reconcile": "8.9 scheduler: leader reads job definitions",
    "scheduler.trigger_dispatch": "8.9 scheduler: resolve the trigger row's company",
    "scheduler.retention_gc": "8.9 scheduler: retention cleanup is platform",
    "scheduler.run_sweep": "8.9 scheduler: stale-run sweep is platform",
    "scheduler.credential_rotation": "8.9 encryption keys: instance key rotation",
    "scheduler.health_check": "interim 8.9 health checks: per-row company split (I-06)",
    "scheduler.mcp_health": "interim 8.9 health checks: per-row company split (I-06)",
    "marketplace.k_skill_sync": "8.9 marketplace system items, K-skill sync",
    # TODO(8.9 company overrides): once system_llm_settings gains tenant rows,
    # this read must filter ``tenant_id IS NULL`` (or move to a tenant session).
    "system_llm.resolve": "8.9 system LLM: platform default (company override later)",
    "audit.platform_event": "8.9 audit: platform events (denied before a company)",
    "skill_evaluation.reconcile": "8.9 restart: leader lists companies of interrupted runs (H-07)",
    "skill_evaluation.system_skill": "8.9 system skills have no company: platform scope (H-07)",
    "authz.shadow_diffs": "8.9 instance-wide shadow observation (no tenant_id; dropped m91)",
    "authz.reconcile": "8.6 item 8 reconcile/rebuild read every company and the outbox",
    "authz.outbox_drain": "8.6 item 6 leader drain of the instance-wide outbox",
    "authz.outbox_apply": "8.6 item 2 post-commit apply-now of the rows a request queued",
    "authz.expiry_cleanup": "8.6 item 7 leader expiry of every company's grants + pruning",
    "authz.backlog_alert": "14.3 instance-wide outbox lag and stuck-marker alert",
    "authz.ops": "14.3 S27 operator sync screen: instance-wide outbox, markers, runs",
    "authz.revocation_overlay": "8.6 step 4 instance-wide pending-marker hint + overlay",
    "authz.delegation_validate": "8.6 delegation sweep: list companies, then per-company sessions",
    "authz.delegation_retry": "8.6 delegation check: leader polls finalized markers",
    "conversation_run.lifecycle": "interim run-id keyed lifecycle writes (I-06 threading)",
    "conversation_run.queue": "interim queue claim/recovery; dispatch resolves company",
}

# Unbound company sessions fail closed under RLS. The request session is bound
# by ``get_org_context``; the rest are derived reads whose company is not yet
# threaded through and must be bound before I-06 enables policies.
UNBOUND_TENANT_SITES: Counter[str] = Counter(
    {
        "dependencies.py": 1,
        "mcp/apps_projection_store.py": 1,
    }
)

# (file, call) -> count. 8.8: advisory-lock leader election and migrations.
ENGINE_ALLOWLIST: Counter[tuple[str, str]] = Counter(
    {
        ("scheduler.py", "engine.connect"): 1,
        ("services/skill_evaluation_worker_leader.py", "engine.connect"): 1,
        ("authz/cli.py", "create_async_engine"): 1,
        ("authz/cli.py", "AsyncSession"): 1,
    }
)
ENGINE_CALLS = frozenset(
    {"AsyncSession", "Session", "async_sessionmaker", "sessionmaker", "create_async_engine"}
)
# SQL text: is_local must be the literal ``true`` (not ``false`` nor a bind
# parameter); ``SET [LOCAL] app.*`` is never used.
SESSION_SETTING = re.compile(
    r"set_config\(\s*'[^']*'\s*,[^,)]*,(?!\s*true\s*\))[^)]*\)|\bSET\s+(LOCAL\s+)?app\.", re.I
)


def _sources() -> list[tuple[str, ast.Module, str]]:
    found = []
    for path in sorted(APP.rglob("*.py")):
        if path != DATABASE:
            source = path.read_text()
            found.append((path.relative_to(APP).as_posix(), ast.parse(source), source))
    return found


def _raw_names(sources: list[tuple[str, ast.Module, str]]) -> frozenset[str]:
    names = {"async_session"}
    for _rel, tree, _source in sources:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.database":
                names.update(a.asname or a.name for a in node.names if a.name == "async_session")
    for _rel, tree, _source in sources:
        for node in tree.body:
            if isinstance(node, ast.Assign) and _is_raw(node.value, frozenset(names)):
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return frozenset(names)


def _is_raw(node: ast.AST, raw: frozenset[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in raw
    if isinstance(node, ast.Attribute):
        return node.attr in raw
    if isinstance(node, ast.Call):  # call-time lookups: stream_attr("async_session")
        return any(isinstance(a, ast.Constant) and a.value in raw for a in node.args)
    return False


def _callee(node: ast.Call) -> str:
    func = node.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")


def _violations(rel: str, tree: ast.Module, raw: frozenset[str]) -> list[str]:
    allowed: set[int] = set()
    for node in tree.body:  # module-level patch-surface aliases
        if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
            allowed.add(id(node.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _callee(node) in SCOPED:
            for value in (k.value for k in node.keywords if k.arg == "maker"):
                parts = value.values if isinstance(value, ast.BoolOp) else [value]
                allowed.update(id(part) for part in (value, *parts))
        if isinstance(node, ast.Compare) and all(
            isinstance(op, ast.Is | ast.IsNot) for op in node.ops
        ):
            allowed.update(id(part) for part in (node.left, *node.comparators))
    found = []
    for node in ast.walk(tree):
        is_load = not isinstance(getattr(node, "ctx", ast.Load()), ast.Store)
        if is_load and _is_raw(node, raw) and id(node) not in allowed:
            found.append(f"{rel}:{getattr(node, 'lineno', 0)}: {ast.unparse(node)}")
    return found


def test_no_unscoped_session_factory_escapes_database_module() -> None:
    sources = _sources()
    raw = _raw_names(sources)
    found = [v for rel, tree, _source in sources for v in _violations(rel, tree, raw)]
    assert found == []


def test_platform_sessions_name_a_documented_reason() -> None:
    found = []
    for rel, tree, _source in _sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _callee(node) in PLATFORM:
                reason = node.args[0] if node.args else None
                if not (isinstance(reason, ast.Constant) and reason.value in PLATFORM_REASONS):
                    found.append(f"{rel}:{node.lineno}: {ast.unparse(node)}")
    assert found == []


def _engine_name(node: ast.AST) -> str | None:
    """``engine`` or any ``<module>.engine`` (e.g. ``database.engine``)."""
    if isinstance(node, ast.Name) and node.id == "engine":
        return node.id
    if isinstance(node, ast.Attribute) and node.attr == "engine":
        return ast.unparse(node)
    return None


def test_engine_detection_covers_attribute_access() -> None:
    tree = ast.parse("async def f():\n    await database.engine.connect()\n    engine.begin()\n")
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    found = [c for c in calls if isinstance(c.func, ast.Attribute) and _engine_name(c.func.value)]
    assert len(found) == 2


def test_direct_engine_connections_are_limited_to_the_documented_exceptions() -> None:
    seen: Counter[tuple[str, str]] = Counter()
    for rel, tree, _source in _sources():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _callee(node)
            func = node.func
            if name in {"connect", "begin"} and isinstance(func, ast.Attribute):
                if _engine_name(func.value) is not None:
                    seen[(rel, f"engine.{name}")] += 1
            elif name in ENGINE_CALLS and not isinstance(func, ast.Attribute):
                seen[(rel, name)] += 1
    assert seen == ENGINE_ALLOWLIST


def _non_local_set_config(tree: ast.Module) -> list[int]:
    """Python-level ``set_config`` calls must pass a literal ``True`` is_local."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _callee(node) == "set_config":
            third = node.args[2] if len(node.args) > 2 else None
            if not (isinstance(third, ast.Constant) and third.value is True):
                found.append(getattr(node, "lineno", 0))
    return found


def test_set_config_detector_rejects_bound_is_local() -> None:
    tree = ast.parse("func.set_config('app.tenant_id', t, flag)\nfunc.set_config('a', t, True)\n")
    assert _non_local_set_config(tree) == [1]
    assert SESSION_SETTING.search("SELECT set_config('app.tenant_id', :t, :is_local)")
    assert SESSION_SETTING.search("SELECT set_config('app.tenant_id', :t, false)")
    assert SESSION_SETTING.search("SET LOCAL app.tenant_id = 'x'")
    assert not SESSION_SETTING.search("SELECT set_config('app.tenant_id', :t, true)")


def test_company_settings_are_never_session_wide() -> None:
    found = [rel for rel, _tree, source in _sources() if SESSION_SETTING.search(source)]
    found += [rel for rel, tree, _source in _sources() if _non_local_set_config(tree)]
    found += ["database.py"] if SESSION_SETTING.search(DATABASE.read_text()) else []
    assert found == []


def test_unbound_company_sessions_are_tracked() -> None:
    seen: Counter[str] = Counter()
    for rel, tree, _source in _sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _callee(node) == "tenant_session" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and first.value is None:
                    seen[rel] += 1
    assert seen == UNBOUND_TENANT_SITES
