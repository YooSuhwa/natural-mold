"""Result and audit shape of a member removal (plan 10.7, S15; E-02)."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.member_exit_settlement import ExitSettlementResult
from app.authz.resource_lifecycle import LifecycleChange
from app.models.user import User
from app.schemas.org_member_exit import MemberRemovalResult


@dataclass(frozen=True, slots=True)
class MemberRemovalOutcome:
    user_id: UUID
    settlement: ExitSettlementResult

    @property
    def change(self) -> LifecycleChange:
        """Apply after commit (8.6 item 2)."""
        return LifecycleChange(tuple(self.settlement.outbox_ids))

    def view(self) -> MemberRemovalResult:
        return MemberRemovalResult(
            user_id=self.user_id,
            status="removed",
            transferred=self.settlement.transferred,
            deleted=self.settlement.deleted,
            revoked_grant_ids=self.settlement.revoked_grant_ids,
        )


def exit_metadata(result: ExitSettlementResult) -> dict[str, object]:
    """``org.member_remove`` audit facts; per-resource events carry the details."""
    return {
        "transferred": [str(value) for value in result.transferred],
        "deleted": [str(value) for value in result.deleted],
        "revoked_grants": len(result.revoked_grant_ids),
    }


async def clear_active_org(db: AsyncSession, user_id: UUID, org_id: UUID) -> None:
    """The removed member's saved organization preference falls back on next request."""
    await db.execute(
        update(User)
        .where(User.id == user_id, User.last_active_org_id == org_id)
        .values(last_active_org_id=None)
    )
