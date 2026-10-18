"""JSON + Markdown reports for the I-05 harnesses (results only, never secrets)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from i05_stats import MetricResult

FindingVerdict = Literal["pass", "fail", "warn", "skipped"]


@dataclass(frozen=True, slots=True)
class Finding:
    check: str
    target: str
    verdict: FindingVerdict
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _ms(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _title(base: str, meta: dict[str, Any]) -> str:
    label = meta.get("label")
    return f"# {base} ({label})" if label else f"# {base}"


def load_markdown(meta: dict[str, Any], results: Sequence[MetricResult]) -> str:
    lines = [
        _title("I-05 load test report", meta),
        "",
        f"- base URL: `{meta.get('base_url')}`",
        f"- started: {meta.get('started_at')}",
        f"- settings: {meta.get('settings')}",
        "",
        "| metric | verdict | n | errors | p50 ms | p95 ms | p99 ms | target p95 | source |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.name} | {r.verdict} | {r.samples} | {r.errors} ({r.error_rate:.1%}) | "
            f"{_ms(r.p50_ms)} | {_ms(r.p95_ms)} | {_ms(r.p99_ms)} | "
            f"{_ms(r.target.p95_ms)} | {r.target.source} |"
        )
    notes = [f"- **{r.name}**: {'; '.join(r.reasons)}" for r in results if r.reasons]
    if notes:
        lines += ["", "## Reasons", "", *notes]
    return "\n".join(lines) + "\n"


def findings_markdown(meta: dict[str, Any], findings: Sequence[Finding]) -> str:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.verdict] = counts.get(finding.verdict, 0) + 1
    lines = [
        _title("I-05 security probe report", meta),
        "",
        f"- base URL: `{meta.get('base_url')}`",
        f"- started: {meta.get('started_at')}",
        f"- totals: {counts}",
        "",
        "| check | target | verdict | detail |",
        "|---|---|---|---|",
    ]
    order = {"fail": 0, "warn": 1, "skipped": 2, "pass": 3}
    for f in sorted(findings, key=lambda item: (order[item.verdict], item.check, item.target)):
        detail = f.detail.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {f.check} | `{f.target}` | {f.verdict} | {detail} |")
    return "\n".join(lines) + "\n"


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def default_output_dir(kind: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path("output") / "i05" / f"{kind}-{stamp}"


def write_reports(out_dir: Path, stem: str, payload: dict[str, Any], markdown: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", "utf-8")
    md_path.write_text(markdown, "utf-8")
    return [json_path, md_path]
