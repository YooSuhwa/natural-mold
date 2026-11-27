"""Non-disclosing reasons a delegated task cannot continue."""

from typing import Literal


class ChildTaskStopped(Exception):
    def __init__(
        self,
        reason: Literal[
            "revoked", "cancelled", "parent_terminal", "child_terminal", "awaiting_approval"
        ],
    ) -> None:
        self.reason = reason
        super().__init__(reason)
