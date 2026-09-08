"""Partial-acquisition cleanup tests for the live egress proxy."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import e2e_runner_proxy as proxy_module  # noqa: E402
from e2e_runner_proxy import ProxyStartError, start_live_proxy  # noqa: E402
from postgres_manifest_io import RunnerInterrupted  # noqa: E402


class _Process:
    def poll(self) -> None:
        return None


def _inherited() -> dict[str, str]:
    return {
        "PATH": "",
        "HOME": "/tmp",
        "E2E_LLM_BASE_URL": "https://gateway.invalid/v1",
        "E2E_LLM_API_KEY": "upstream-key",
        "E2E_LLM_MODEL": "model",
    }


def test_proxy_sigint_reaps_partial_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = io.BytesIO()
    monkeypatch.setattr(
        proxy_module, "start_owned_process", lambda *_args, **_kwargs: (_Process(), log)
    )
    monkeypatch.setattr(proxy_module, "stop_process", lambda _process: False)
    monkeypatch.setattr(
        proxy_module.time, "sleep", lambda _seconds: (_ for _ in ()).throw(RunnerInterrupted(2))
    )

    with pytest.raises(ProxyStartError) as captured:
        start_live_proxy(SimpleNamespace(run_root=tmp_path), _inherited())  # type: ignore[arg-type]

    assert captured.value.signal_number == 2
    assert captured.value.process_group_stopped is False
    assert log.closed is True


def test_proxy_invalid_ready_reaps_partial_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "proxy-ready.json").write_text("not-json")
    log = io.BytesIO()
    monkeypatch.setattr(
        proxy_module, "start_owned_process", lambda *_args, **_kwargs: (_Process(), log)
    )
    monkeypatch.setattr(proxy_module, "stop_process", lambda _process: True)

    with pytest.raises(ProxyStartError, match="egress_proxy_not_ready") as captured:
        start_live_proxy(SimpleNamespace(run_root=tmp_path), _inherited())  # type: ignore[arg-type]

    assert captured.value.process_group_stopped is True
    assert log.closed is True


def test_proxy_forwards_only_exact_owned_loopback_opt_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Given a live opt-in, when the proxy starts, then its child gets only the exact flag."""
    captured_environment: dict[str, str] = {}
    log = io.BytesIO()

    def start_proxy_child(
        *_args: object, env: dict[str, str], **_kwargs: object
    ) -> tuple[_Process, io.BytesIO]:
        captured_environment.update(env)
        (tmp_path / "proxy-ready.json").write_text(
            '{"proxyBaseUrl":"http://127.0.0.1:43123/v1"}', encoding="utf-8"
        )
        return _Process(), log

    monkeypatch.setattr(proxy_module, "start_owned_process", start_proxy_child)
    inherited = {
        **_inherited(),
        "E2E_EGRESS_ALLOW_OWNED_LOOPBACK": "1",
        "OPENAI_API_KEY": "unrelated-provider-secret",
        "E2E_LLM_MODEL_EXTRA": "unrelated-live-control",
    }

    proxy = start_live_proxy(SimpleNamespace(run_root=tmp_path), inherited)  # type: ignore[arg-type]

    assert captured_environment["E2E_EGRESS_ALLOW_OWNED_LOOPBACK"] == "1"
    assert "E2E_EGRESS_PORT" not in captured_environment
    assert "OPENAI_API_KEY" not in captured_environment
    assert "E2E_LLM_MODEL_EXTRA" not in captured_environment
    assert proxy.base_url == "http://127.0.0.1:43123/v1"
