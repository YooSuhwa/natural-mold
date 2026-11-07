"""Who decides a memory write (memory policy plan 15, 5.2/5.3/5.11, Q9/Q10).

* **Tenant policy** (``tenants.settings["memory"]``): the same keys for a company and a
  personal tenant; only the defaults differ (``memory_policy_defaults``).
* **Approval mode** (the user's, 5.11): a preset over three axes, each ``auto`` /
  ``approve`` / ``off``: new records, profile documents, consolidation.
* **Origin** (5.3-1), decided by the server: ``user_stated`` only for text a person typed
  on the memory screens; a model's tool call is ``agent_inferred`` or, once outside
  content entered the conversation, ``tool_derived``.

``decide_record_write`` turns these into ``save`` / ``propose`` / ``reject``. Floors no
mode lifts: secrets and high-risk personal data are rejected by the checks, and an
instruction-like memory is always a proposal.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from app.models.memory import MEMORY_APPROVAL_MODES, MEMORY_AXIS_VALUES

Axis = Literal["auto", "approve", "off"]
WriteOutcome = Literal["save", "propose", "reject"]
TenantKind = Literal["company", "personal"]

AXES: Final = ("records", "profile", "consolidation")
_PRESETS: Final[dict[str, dict[str, Axis]]] = {
    "off": {"records": "off", "profile": "off", "consolidation": "off"},
    "read_only": {"records": "off", "profile": "off", "consolidation": "off"},
    "approve_all": {"records": "approve", "profile": "approve", "consolidation": "approve"},
    "partial_auto": {"records": "auto", "profile": "approve", "consolidation": "auto"},
    "auto_notify": {"records": "auto", "profile": "auto", "consolidation": "auto"},
}
# Marks that keep a write for a person under ``partial_auto`` (5.11 "표시 붙은 것").
REVIEW_FLAGS: Final = frozenset({"sensitive", "other_person", "instruction_like"})
REVIEW_ORIGINS: Final = frozenset({"tool_derived", "legacy", "consolidation"})


@dataclass(frozen=True, slots=True)
class TenantMemoryPolicy:
    default_mode: str
    allowed_modes: frozenset[str]
    consolidation_enabled: bool
    channel_personal_memory: bool
    contacts_allowed: bool


def memory_policy_defaults(kind: TenantKind) -> dict[str, Any]:
    """The only place company and personal tenants differ (Q9): values, never keys."""
    return {
        "default_mode": "partial_auto" if kind == "personal" else "approve_all",
        "allowed_modes": list(MEMORY_APPROVAL_MODES),
        "consolidation_enabled": kind == "personal",
        "channel_personal_memory": False,
        "contacts_allowed": True,
    }


def tenant_kind(tenant_settings: Mapping[str, Any] | None) -> TenantKind:
    return "personal" if (tenant_settings or {}).get("kind") == "personal" else "company"


def tenant_memory_policy(tenant_settings: Mapping[str, Any] | None) -> TenantMemoryPolicy:
    """Defaults for the tenant's kind, overlaid by valid stored values (invalid ones ignored)."""
    merged = memory_policy_defaults(tenant_kind(tenant_settings))
    stored = (tenant_settings or {}).get("memory")
    if isinstance(stored, Mapping):
        for key in merged:
            if key in stored:
                merged[key] = stored[key]
    allowed = frozenset(
        mode for mode in _strings(merged["allowed_modes"]) if mode in MEMORY_APPROVAL_MODES
    ) or frozenset({"approve_all"})
    default = merged["default_mode"]
    if default not in allowed:
        default = "approve_all" if "approve_all" in allowed else sorted(allowed)[0]
    return TenantMemoryPolicy(
        default_mode=str(default),
        allowed_modes=allowed,
        consolidation_enabled=merged["consolidation_enabled"] is True,
        channel_personal_memory=merged["channel_personal_memory"] is True,
        contacts_allowed=merged["contacts_allowed"] is not False,
    )


def _strings(value: object) -> Iterable[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def effective_mode(
    *,
    memory_enabled: bool,
    approval_mode: str | None,
    legacy_write_policy: str,
    tenant: TenantMemoryPolicy,
) -> str:
    """The user's mode within the tenant's allowed set.

    Settings saved before plan 15 have no mode: ``off`` becomes ``read_only``, ``auto``
    ``partial_auto``, and the old default ``ask`` takes the tenant default.
    """
    if not memory_enabled:
        return "off"
    mode = approval_mode
    if mode is None:
        mode = {"off": "read_only", "auto": "partial_auto"}.get(
            legacy_write_policy, tenant.default_mode
        )
    if mode not in tenant.allowed_modes:
        return tenant.default_mode
    return mode


def mode_axes(mode: str, custom: Mapping[str, Any] | None = None) -> dict[str, Axis]:
    if mode == "custom":
        axes: dict[str, Axis] = {}
        for axis in AXES:
            value = (custom or {}).get(axis)
            axes[axis] = value if value in MEMORY_AXIS_VALUES else "approve"  # type: ignore[assignment]
        return axes
    return dict(_PRESETS.get(mode, _PRESETS["approve_all"]))


@dataclass(frozen=True, slots=True)
class WriteDecision:
    outcome: WriteOutcome
    reason: str | None = None
    review_later: bool = False  # ``auto_notify``: saved, listed under "나중에 검토"


def decide_record_write(
    *,
    mode: str,
    axes: Mapping[str, Axis],
    run_path: str,
    origin: str,
    flags: Iterable[str],
    trigger_writes_enabled: bool = True,
) -> WriteDecision:
    """A new record from a model tool call or the memory screens (5.2 write table)."""
    marks = frozenset(flags)
    if origin == "user_stated":
        return WriteDecision("save")  # typed by a person on the memory screens
    if run_path == "trigger":
        # A schedule never saves on its own (5.2): its owner approves.
        if not trigger_writes_enabled:
            return WriteDecision("reject", "memory_write_disabled")
        return WriteDecision("propose")
    if run_path != "chat":
        return WriteDecision("reject", "memory_write_disabled")
    axis = axes.get("records", "approve")
    if axis == "off":
        return WriteDecision("reject", "memory_write_disabled")
    if "instruction_like" in marks:
        return WriteDecision("propose")  # floor: every mode asks a person
    if axis == "approve":
        return WriteDecision("propose")
    flagged = bool(marks & REVIEW_FLAGS) or origin in REVIEW_ORIGINS
    if not flagged:
        return WriteDecision("save")
    if mode == "auto_notify":
        return WriteDecision("save", review_later=True)
    return WriteDecision("propose")


__all__ = [
    "AXES",
    "TenantMemoryPolicy",
    "WriteDecision",
    "decide_record_write",
    "effective_mode",
    "memory_policy_defaults",
    "mode_axes",
    "tenant_kind",
    "tenant_memory_policy",
]
