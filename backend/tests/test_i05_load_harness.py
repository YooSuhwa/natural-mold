"""I-05 load harness logic: percentiles, target verdicts, guards, runner, CLI refusal."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import i05_load_test  # noqa: E402
from i05_load_runner import (  # noqa: E402
    LoadSettings,
    apply_overrides,
    missing_needs,
    run_scenario,
    select,
)
from i05_load_scenarios import LoadContext, Scenario, catalog  # noqa: E402
from i05_safety import GuardError, check_target, parse_config  # noqa: E402
from i05_stats import Target, evaluate, overall_failed, percentile  # noqa: E402


def test_percentile_interpolates_linearly_like_numpy() -> None:
    samples = [float(v) for v in range(1, 101)]  # 1..100
    assert percentile(samples, 50) == pytest.approx(50.5)
    assert percentile(samples, 95) == pytest.approx(95.05)
    assert percentile(samples, 99) == pytest.approx(99.01)
    assert percentile([7.0], 95) == 7.0
    assert percentile([3.0, 1.0, 2.0], 0) == 1.0
    assert percentile([3.0, 1.0, 2.0], 100) == 3.0


def test_percentile_rejects_empty_or_out_of_range() -> None:
    with pytest.raises(ValueError, match="empty"):
        percentile([], 50)
    with pytest.raises(ValueError, match="outside"):
        percentile([1.0], 101)


def test_evaluate_fails_when_p95_exceeds_target() -> None:
    result = evaluate("fga.check", [10.0] * 90 + [30.0] * 10, 0, Target(p95_ms=20))
    assert result.verdict == "fail"
    assert "p95" in result.reasons[0]


def test_evaluate_passes_at_exact_bound_and_measures_without_bound() -> None:
    assert evaluate("x", [20.0] * 10, 0, Target(p95_ms=20)).verdict == "pass"
    assert evaluate("x", [500.0] * 10, 0, Target()).verdict == "measured"


def test_evaluate_fails_on_error_rate_and_on_no_samples() -> None:
    errors = evaluate("x", [1.0] * 99, 1, Target(p95_ms=20))
    assert errors.verdict == "fail" and errors.error_rate == pytest.approx(0.01)
    tolerant = evaluate("x", [1.0] * 99, 1, Target(p95_ms=20, max_error_rate=0.02))
    assert tolerant.verdict == "pass"
    empty = evaluate("x", [], 3, Target())
    assert empty.verdict == "fail" and "no successful samples" in empty.reasons
    assert overall_failed([tolerant, empty])


@pytest.mark.parametrize(
    ("url", "ack", "message"),
    [
        ("https://staging.example.com", False, "i-know-this-is-staging"),
        ("http://staging.example.com", True, "https"),
        ("http://localhost:5432", False, "database"),
        ("https://user:pw@staging.example.com", True, "credentials"),
        ("postgresql://localhost/moldy", False, "http(s)"),
        ("https://staging.example.com/?x=1", True, "query"),
    ],
)
def test_guard_refuses_unsafe_targets(url: str, ack: bool, message: str) -> None:
    with pytest.raises(GuardError, match=message.replace("(", r"\(").replace(")", r"\)")):
        check_target(url, staging_ack=ack)


def test_guard_allows_local_and_acknowledged_staging() -> None:
    assert check_target("http://127.0.0.1:8001/", staging_ack=False) == "http://127.0.0.1:8001"
    assert check_target("https://stg.example.com", staging_ack=True) == "https://stg.example.com"


def test_config_refuses_literal_secrets_but_accepts_env_names_and_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("I05_A_PW", raising=False)
    with pytest.raises(GuardError, match="literal secrets"):
        parse_config({"base_url": "http://localhost", "personas": {"a": {"password": "x"}}})
    config = parse_config(
        {
            "base_url": "http://localhost",
            "personas": {"a": {"email": "a@x.test", "password_env": "I05_A_PW"}},
            "security": {"foreign": {"api_key_id": "id-1"}},
        }
    )
    assert config.missing_env() == ["I05_A_PW"]
    monkeypatch.setenv("I05_A_PW", "from-env")
    assert config.missing_env() == []
    assert config.persona("a").password() == "from-env"


def test_default_selection_excludes_opt_in_run_start() -> None:
    chosen, unknown = select(catalog(), [])
    names = {s.name for s in chosen}
    assert "api.run_start" not in names and "fga.batch_check_50" in names
    assert unknown == []
    assert select(catalog(), ["api.run_start", "nope"])[1] == ["nope"]


def test_plan_targets_match_plan_8_10_and_overrides_apply() -> None:
    by_name = {s.name: s for s in catalog()}
    assert by_name["fga.check"].targets["fga.check"].p95_ms == 20
    assert by_name["fga.batch_check_50"].targets["fga.batch_check_50"].p95_ms == 60
    assert by_name["fga.check_concurrent_8"].concurrency == 8
    overridden = apply_overrides(by_name["api.list.agents"], {"api.list.agents": {"p95_ms": 300}})
    assert overridden["api.list.agents"].p95_ms == 300
    assert missing_needs(by_name["fga.check"], {"fga": {"url": "u"}}) == [
        "fga.store_id",
        "fga.model_id",
        "fga.check",
    ]


def test_runner_counts_samples_errors_and_custom_metrics() -> None:
    calls = {"n": 0}

    async def op(ctx: LoadContext) -> dict[str, float] | None:
        calls["n"] += 1
        if calls["n"] % 10 == 0:
            raise RuntimeError("boom")
        return {"m.a": 5.0, "m.b": 50.0}

    scenario = Scenario("s", {"m.a": Target(p95_ms=10), "m.b": Target(p95_ms=10)}, op, ())
    ctx = LoadContext(sessions={}, sections={})
    settings = LoadSettings(concurrency=4, iterations=40, warmup=0, ramp_seconds=0.01)
    results = asyncio.run(run_scenario(scenario, ctx, settings, scenario.targets))
    by_name = {r.name: r for r in results}
    assert calls["n"] == 40
    assert by_name["m.a"].samples == 36 and by_name["m.a"].errors == 4
    assert by_name["m.b"].verdict == "fail"
    assert by_name["m.a"].error_examples[0].startswith("RuntimeError")


def _config(tmp_path: Path, base_url: str) -> Path:
    path = tmp_path / "i05.json"
    personas = {"a": {"email": "a@x.test", "password_env": "I05_TEST_PW_UNSET"}}
    path.write_text(json.dumps({"base_url": base_url, "personas": personas, "load": {"user": "a"}}))
    return path


def test_cli_refuses_remote_without_ack_and_dry_run_never_logs_in(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    remote = _config(tmp_path, "https://staging.example.com")
    assert i05_load_test.main(["--config", str(remote), "--dry-run"]) == 2
    assert "i-know-this-is-staging" in capsys.readouterr().err
    code = i05_load_test.main(["--config", str(remote), "--dry-run", "--i-know-this-is-staging"])
    plan = json.loads(capsys.readouterr().out)
    assert code == 0
    assert plan["missing_env"] == ["I05_TEST_PW_UNSET"]
    assert {s["name"] for s in plan["scenarios"]} >= {"api.list.agents", "share.propagation"}


def test_cli_refuses_real_run_when_secret_env_is_unset(tmp_path: Path) -> None:
    local = _config(tmp_path, "http://localhost:8001")
    assert i05_load_test.main(["--config", str(local), "--scenarios", "api.list.agents"]) == 2
