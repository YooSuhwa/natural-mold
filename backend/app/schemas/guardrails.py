"""Guardrail policy, templates and agent bindings (guardrail-minimum-plan.md §3.4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

PolicyMode = Literal["off", "open", "default", "enforced"]
RiskLevel = Literal["read_only", "write_internal", "external_mutation", "code_execution", "unknown"]
RiskAction = Literal["allow", "approval", "approval_relaxable", "block"]
BindingMode = Literal["inherit", "template", "off"]


class GuardrailRuleOut(BaseModel):
    id: str
    packs: list[str]
    stages: list[str]
    action: str
    entities: list[str] | None
    restore_for_tools: bool
    pseudonym_style: str


class GuardrailTemplateChoice(BaseModel):
    """A template a policy or an agent can point at."""

    ref: str  # "builtin:<name>" or "company:<uuid>"
    name: str
    kind: Literal["builtin", "company"]
    version: int
    rules: list[GuardrailRuleOut]


class GuardrailPolicyOut(BaseModel):
    mode: PolicyMode
    default_template: str | None
    locked_rules: list[str]
    # Every tool risk level with the company's action (defaults filled in).
    tool_risk: dict[RiskLevel, RiskAction]
    # Tools a person always approves, whatever an agent's settings say (approval-modes-plan Q2).
    always_ask_tools: list[str] = Field(default_factory=list)
    # False: the company has not saved a policy; the installation default applies.
    saved: bool
    templates: list[GuardrailTemplateChoice]
    updated_at: datetime


class GuardrailPolicyUpdate(BaseModel):
    mode: PolicyMode
    default_template: str | None = Field(default=None, max_length=80)
    locked_rules: list[str] = Field(default_factory=list, max_length=40)
    # Omitted: the saved tool risk actions stay as they are.
    tool_risk: dict[RiskLevel, RiskAction] | None = None
    always_ask_tools: list[Annotated[str, Field(min_length=1, max_length=128)]] | None = Field(
        default=None, max_length=200
    )


class GuardrailTemplateOut(BaseModel):
    id: uuid.UUID
    ref: str
    name: str
    description: str
    status: Literal["draft", "published", "archived"]
    draft_rules: list[dict[str, Any]]
    published_version: int | None
    updated_at: datetime


class GuardrailTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    # Start from another template's rules ("builtin:kr-default") or from ``rules``.
    copy_from: str | None = Field(default=None, max_length=80)
    rules: list[dict[str, Any]] | None = None


class GuardrailTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    rules: list[dict[str, Any]] | None = None


class GuardrailTemplateVersionOut(BaseModel):
    """One published version; never changes after publishing."""

    version: int
    rules: list[GuardrailRuleOut]
    published_at: datetime
    published_by: uuid.UUID | None


class GuardrailTemplateRevert(BaseModel):
    """Publish an earlier version's rules again, as the next version."""

    version: int = Field(ge=1)


class GuardrailRuleOverride(BaseModel):
    enabled: bool = True
    action: Literal["block", "redact", "hash", "pseudonymize", "mask", "warn"] | None = None


class AgentGuardrailOut(BaseModel):
    mode: BindingMode
    template_ref: str | None
    rule_overrides: dict[str, GuardrailRuleOverride]
    policy_mode: PolicyMode
    # The company default the agent inherits (its rules are what overrides apply to).
    policy_default_template: str | None
    locked_rules: list[str]
    # The company's tool risk actions, which the agent cannot change.
    tool_risk: dict[RiskLevel, RiskAction]
    # What the agent's next run gets (None: no guardrail).
    effective: GuardrailTemplateChoice | None
    templates: list[GuardrailTemplateChoice]


class AgentGuardrailUpdate(BaseModel):
    mode: BindingMode
    template_ref: str | None = Field(default=None, max_length=80)
    rule_overrides: dict[str, GuardrailRuleOverride] = Field(default_factory=dict, max_length=40)
