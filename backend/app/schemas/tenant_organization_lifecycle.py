"""Organization archive/restore request models (no successor or default fields)."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

# Exact, unstripped comparison against the stored organization name.
ConfirmationName = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class TenantOrganizationArchive(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    confirm_name: ConfirmationName
