"""Check the compiled frontend transport independently of the Playwright driver's mode."""

# ruff: noqa: S101 -- pytest assertions.
from contextlib import ExitStack, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import run


@pytest.mark.parametrize("verify_default", [False, True])
def test_transport_overrides_for_build_start_and_driver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verify_default: bool
) -> None:
    flag = "NEXT_PUBLIC_CHAT_RUNTIME"
    modes = {"ag_ui": "http://localhost:3001", "langgraph_v3": "http://localhost:3002"}
    env = {flag: "inherited", "E2E_API_BASE_URL": "http://localhost:8001"}
    processes: dict[str, dict[str, str]] = {}

    def command(
        name: str, args: list[str], cwd: Path, cfg: dict[str, str], logs: Path, timeout: int = 300
    ) -> None:
        processes[name] = dict(cfg)
        if args[2:4] == ["playwright", "test"]:
            Path(cfg["MOLDY_AGUI_PLAYWRIGHT_JSON"]).write_text("{}")

    def managed(
        name: str, args: list[str], cwd: Path, cfg: dict[str, str], logs: Path
    ) -> nullcontext[tuple[SimpleNamespace, Path]]:
        processes[name] = dict(cfg)
        return nullcontext((SimpleNamespace(pid=10), logs / name))

    reporter = Mock(return_value={"passed": True})
    monkeypatch.setattr(run, "command", command)
    monkeypatch.setattr(run, "managed", managed)
    monkeypatch.setattr(run, "wait_ready", Mock())
    monkeypatch.setattr(run, "playwright_report", reporter)
    record: dict = {"processes": {}, "browser": {}}
    with ExitStack() as stack:
        run.browser_gate(
            modes,
            env,
            tmp_path,
            tmp_path,
            tmp_path,
            stack,
            [],
            [],
            [],
            verify_default,
            tmp_path,
            record,
            Mock(),
        )

    for mode in modes:
        for stage in ("build", "next"):
            actual = processes[f"{stage}-{mode}"]
            if verify_default and mode == "ag_ui":
                assert flag not in actual
            else:
                assert actual[flag] == mode
        assert record["frontend_transport_environment"][mode] == {
            "build_and_start_override": None if verify_default and mode == "ag_ui" else mode,
            "expected_browser_transport": mode,
        }
    groups = {
        "performance-ag_ui": "ag_ui",
        "cutover": "ag_ui",
        "performance-langgraph_v3": "langgraph_v3",
    }
    assert {call.kwargs["group"] for call in reporter.call_args_list} == set(groups)
    for group, mode in groups.items():
        cfg = processes[group]
        assert cfg[flag] == mode
        assert cfg["E2E_BASE_URL"] == modes[mode]
        assert cfg["MOLDY_AGUI_PLAYWRIGHT_GROUP"] == group
        assert Path(cfg["MOLDY_AGUI_PLAYWRIGHT_JSON"]) == tmp_path / f"pw-{group}.json"
