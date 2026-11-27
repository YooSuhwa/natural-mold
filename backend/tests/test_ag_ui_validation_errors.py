"""Public run validation reports the input field without reflecting submitted values."""

import pytest
from httpx import AsyncClient

from tests.integration._seed import seed_conversation_with_agent


@pytest.mark.parametrize(
    "invalid_message",
    [
        {"id": "msg", "role": "confidential-discriminator", "content": "confidential-text"},
        {
            "id": "msg",
            "role": "user",
            "content": [{"type": "confidential-discriminator", "text": "confidential-text"}],
        },
        {
            "id": "msg",
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "confidential-discriminator", "confidential-key": "value"},
                }
            ],
        },
    ],
)
async def test_invalid_ag_ui_union_only_identifies_public_message_field(
    client: AsyncClient, invalid_message: dict[str, object]
) -> None:
    conversation_id = await seed_conversation_with_agent()
    response = await client.post(
        f"/api/conversations/{conversation_id}/ag-ui/runs",
        json={"threadId": str(conversation_id), "runId": "public", "messages": [invalid_message]},
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"] == [{"loc": ["body", "messages"], "type": "invalid"}]
    assert "confidential" not in response.text


async def test_invalid_ag_ui_json_and_missing_fields_keep_safe_diagnostics(
    client: AsyncClient,
) -> None:
    conversation_id = await seed_conversation_with_agent()
    url = f"/api/conversations/{conversation_id}/ag-ui/runs"
    malformed = await client.post(
        url, content=b'{"messages": confidential}', headers={"Content-Type": "application/json"}
    )
    assert malformed.status_code == 422
    assert malformed.json()["error"]["details"] == [{"loc": ["body"], "type": "invalid"}]
    assert "confidential" not in malformed.text

    missing = await client.post(url, json={"threadId": str(conversation_id), "messages": []})
    assert missing.status_code == 422
    assert missing.json()["error"]["details"] == [{"loc": ["body", "runId"], "type": "missing"}]


async def test_unrelated_route_keeps_per_field_validation_message(client: AsyncClient) -> None:
    conversation_id = await seed_conversation_with_agent()
    response = await client.patch(
        f"/api/conversations/{conversation_id}", json={"title": ["value"]}
    )
    assert response.status_code == 422
    detail = response.json()["error"]["details"][0]
    assert detail["loc"] == ["body", "title"]
    assert detail["type"] == "string_type" and detail["msg"]
