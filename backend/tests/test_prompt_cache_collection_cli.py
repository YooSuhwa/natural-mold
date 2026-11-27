"""A local CLI invocation may spend only with the explicit dedicated-project settings."""

from __future__ import annotations

import sys
from argparse import Namespace
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import settings
from scripts import collect_prompt_cache


@pytest.mark.parametrize(
    "case",
    [
        "no_consent",
        "wrong_org",
        "missing_trial_key",
        "floating_model",
        "invalid_price",
        "authorized",
    ],
)
def test_trial_cli_dispatch(case: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    org = uuid4()
    monkeypatch.setattr(settings, "openai_prompt_cache_org_ids", frozenset({org}))
    monkeypatch.setenv("OPENAI_API_KEY", "shared-key-must-never-fund-the-trial")
    if case == "missing_trial_key":
        monkeypatch.delenv("MOLDY_CACHE_TRIAL_OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("MOLDY_CACHE_TRIAL_OPENAI_API_KEY", "dedicated-fixture")
    output = tmp_path / "trial.jsonl"
    args = [
        "collect_prompt_cache",
        str(output),
        "--org-id",
        str(uuid4() if case == "wrong_org" else org),
        "--model-snapshot",
        "gpt-4o-mini" if case == "floating_model" else "gpt-4o-mini-2024-07-18",
        "--input-usd-per-million",
        "bad" if case == "invalid_price" else "2.5",
        "--cached-input-usd-per-million",
        "1.0",
    ]
    if case != "no_consent":
        args.append("--execute-paid-trial")

    calls = []

    async def capture(parsed: Namespace, key: str) -> None:
        calls.append((parsed.org_id, parsed.output, parsed.input_usd_per_million, key))

    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(collect_prompt_cache, "_run", capture)
    if case == "authorized":
        collect_prompt_cache.main()
        assert calls == [(org, output, Decimal("2.5"), "dedicated-fixture")]
    else:
        with pytest.raises(SystemExit) as stopped:
            collect_prompt_cache.main()
        assert stopped.value.code == 2
        assert not calls
    assert not output.exists()
