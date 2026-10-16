"""The subject of an authorization decision, fixed at decision time (plan 8.1)."""

from dataclasses import dataclass
from ipaddress import ip_address
from typing import Literal
from uuid import UUID

from app.authz.org_context import OrgContext

RunSource = Literal["CHAT", "TRIGGER", "CHANNEL", "SUBAGENT", "API"]


@dataclass(frozen=True, slots=True)
class Principal:
    kind: Literal["user", "agent"]
    id: UUID
    org_id: UUID
    run_source: RunSource = "CHAT"
    user_ip: str | None = None
    # An agent principal acting for a user (delegated run): revocations of that user
    # also refuse the agent's decisions (D12 deny overlay).
    acting_user_id: UUID | None = None

    def __post_init__(self) -> None:
        """Unparseable client IPs fall back to the 7.3 sentinel instead of failing a query."""
        try:
            normalized = str(ip_address(self.user_ip)) if self.user_ip else None
        except ValueError:
            normalized = None
        object.__setattr__(self, "user_ip", normalized)

    def fga(self) -> str:
        return f"{self.kind}:{self.id}"

    def revocation_subjects(self) -> frozenset[str]:
        """Subjects whose pending revocation markers apply to this principal."""
        acting = {f"user:{self.acting_user_id}"} if self.acting_user_id else set()
        return frozenset({self.fga(), *acting})

    @classmethod
    def for_user(cls, ctx: OrgContext, *, user_ip: str | None = None) -> "Principal":
        """HTTP requests act as the current organization's user with run source CHAT."""
        return cls(kind="user", id=ctx.user_id, org_id=ctx.org_id, user_ip=user_ip)
