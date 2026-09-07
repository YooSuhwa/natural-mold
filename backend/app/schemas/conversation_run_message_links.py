from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

ConversationRunMessageKey = Annotated[str, StringConstraints(min_length=1, max_length=512)]


class ConversationRunMessageLinkResponse(BaseModel):
    """A durable public message identifier mapped to its canonical run."""

    model_config = ConfigDict(frozen=True)

    message_id: ConversationRunMessageKey
    run_id: uuid.UUID
