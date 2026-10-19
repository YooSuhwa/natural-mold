"""Dry-run link verdicts for the readiness check (plan 12.1, 11.2; G-01).

``ReadinessPlanner.verdicts(agent)`` asks exactly what ``RuntimeAccessPlanner.plan`` asks
(same classification, same BatchCheck per principal, same 11.1 subject) but never raises
``agent_not_ready``: it returns which links (and whether the model) a run would be
denied, so the readiness service can answer every 12.1 row. No credential is resolved
or decrypted here. While nothing is enforced the legacy answer is "every link allowed"
(the owner-scoped load is the legacy gate).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from app.authz.runtime_access_planner import Reason, RuntimeAccessPlanner
from app.credentials.run_subject import RunSubject, subject_for_agent
from app.models.agent import Agent


@dataclass(frozen=True, slots=True)
class LinkVerdicts:
    subject: RunSubject
    # (planner kind ``tool``/``mcp_tool``/``skill``/``agent``, linked row id) -> reason.
    denied: Mapping[tuple[str, UUID], Reason] = field(default_factory=dict)
    model_denied: bool = False
    # The planner's effective ``required`` per decided link (partial enforcement forces
    # a non-owned link of a type not yet enforced to required, as the run does).
    required: Mapping[tuple[str, UUID], bool] = field(default_factory=dict)
    # FGA objects confirmed here (the credential resolver does not ask them again).
    decided: frozenset[str] = frozenset()

    def reason(self, kind: str, target: UUID) -> Reason | None:
        return self.denied.get((kind, target))

    def required_of(self, kind: str, target: UUID, declared: bool) -> bool:
        return self.required.get((kind, target), bool(declared))


class ReadinessPlanner(RuntimeAccessPlanner):
    async def verdicts(self, agent: Agent, *, root: bool = True) -> LinkVerdicts:
        """``root``: the caller's ``agent#can_run`` is required (404/403 as for runs)."""
        subject = subject_for_agent(agent, self.context, self.identity)
        if not self.enforced():
            return LinkVerdicts(subject)
        draft, allowed = await self._assess(agent, subject, root)
        denied: dict[tuple[str, UUID], Reason] = {}
        required: dict[tuple[str, UUID], bool] = {}
        decided: set[str] = set()
        model_denied = False
        for item in draft.pending:
            if item.link is not None:
                required[(item.kind, UUID(item.obj.partition(":")[2]))] = item.required
            if item.obj in allowed:
                decided.update((item.obj, *item.implied))
            elif item.link is None:
                model_denied = True
            else:
                denied[(item.kind, UUID(item.obj.partition(":")[2]))] = item.reason
        return LinkVerdicts(subject, denied, model_denied, required, frozenset(decided))


__all__ = ["LinkVerdicts", "ReadinessPlanner"]
