from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.chat_resource_context import ChatResourceContextRequest, ChatResourceReference
from app.services.chat_resource_context_errors import ResourceContextNotFoundError
from app.services.thread_branch_service import MessageTree, MessageTreeNode
from tests.test_chat_resource_context_support import (
    resource_context_resolver,
    seed_resource_context,
)


@pytest.mark.asyncio
async def test_quote_freezes_only_selected_message_and_keeps_provenance(
    db: AsyncSession, tmp_path: Path
) -> None:
    seed = await seed_resource_context(db)
    reference = ChatResourceReference(
        kind="conversation",
        id=seed.conversation.id,
        message_id="answer-1",
        quote="선택한 문장",
        comment="더 자세히 알려줘",
        label="forged title",
        message_role="user",
    )
    tree = MessageTree(
        nodes=[
            MessageTreeNode(
                message=AIMessage(id="answer-1", content="앞 문단\n선택한 문장\n뒤 문단"),
                parent_id=None,
                introduced_by_checkpoint_id="checkpoint-1",
            )
        ],
        active_tip_message_id="answer-1",
        active_checkpoint_id="checkpoint-1",
    )
    with (
        patch("app.agent_runtime.checkpointer.get_checkpointer"),
        patch(
            "app.services.thread_branch_service.build_message_tree", AsyncMock(return_value=tree)
        ),
    ):
        resolver = resource_context_resolver(db, seed, tmp_path)
        result = await resolver.resolve(ChatResourceContextRequest(resources=[reference]))
        item = result.resources[0]
        assert item.quote == "선택한 문장"
        assert item.message_id == "answer-1"
        assert item.message_role == "assistant"
        assert item.resolved_label == "Current"
        assert "뒤 문단" not in item.text
        assert "더 자세히 알려줘" in item.text
        assert item.to_public_reference().get("quote") == "선택한 문장"
        assert await resolver.reauthorize(result) == result

        with pytest.raises(ResourceContextNotFoundError):
            await resolver.resolve(
                ChatResourceContextRequest(
                    resources=[
                        reference.model_copy(update={"message_id": "other-message"}),
                    ]
                )
            )


@pytest.mark.parametrize(
    "fields",
    [
        {"message_id": "m"},
        {"quote": "text"},
        {"comment": "alone"},
        {"message_id": "m", "quote": ""},
    ],
)
def test_quote_requires_message_and_nonempty_excerpt(fields: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        ChatResourceReference.model_validate(
            {
                "kind": "conversation",
                "id": "11111111-1111-4111-8111-111111111111",
                **fields,
            }
        )


def test_distinct_excerpts_from_one_message_are_not_duplicates() -> None:
    common = {
        "kind": "conversation",
        "id": "11111111-1111-4111-8111-111111111111",
        "message_id": "m",
    }
    request = ChatResourceContextRequest.model_validate(
        {
            "resources": [
                {**common, "quote": "first"},
                {**common, "quote": "second"},
            ]
        }
    )
    assert len(request.resources) == 2
