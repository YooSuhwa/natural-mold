"""Tests for standardized error handling."""

import pytest

from app.exceptions import (
    AppError,
    ExternalServiceError,
    NotFoundError,
    ValidationError,
)


class TestExceptionHierarchy:
    def test_app_error_attributes(self):
        err = AppError("TEST_ERROR", "Test message", status=400)
        assert err.code == "TEST_ERROR"
        assert err.message == "Test message"
        assert err.status == 400
        assert str(err) == "Test message"

    def test_not_found_error(self):
        err = NotFoundError("AGENT_NOT_FOUND", "에이전트를 찾을 수 없습니다")
        assert err.status == 404
        assert isinstance(err, AppError)

    def test_validation_error(self):
        err = ValidationError("INVALID_INPUT", "잘못된 입력입니다")
        assert err.status == 422
        assert isinstance(err, AppError)

    def test_external_service_error(self):
        err = ExternalServiceError("MCP_ERROR", "MCP 서버 오류")
        assert err.status == 502
        assert isinstance(err, AppError)


@pytest.mark.asyncio
class TestErrorHandlers:
    async def test_not_found_returns_404_with_code(self, client):
        response = await client.get("/api/agents/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404
        body = response.json()
        assert "error" in body
        assert body["error"]["code"] == "AGENT_NOT_FOUND"

    async def test_model_not_found(self, client):
        response = await client.delete("/api/models/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "MODEL_NOT_FOUND"

    async def test_tool_not_found(self, client):
        response = await client.delete("/api/tools/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "TOOL_NOT_FOUND"

    async def test_conversation_not_found(self, client):
        response = await client.get(
            "/api/conversations/00000000-0000-0000-0000-000000000000/messages"
        )
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "CONVERSATION_NOT_FOUND"

    async def test_error_response_structure(self, client):
        response = await client.get("/api/agents/00000000-0000-0000-0000-000000000000")
        body = response.json()
        assert "error" in body
        assert "code" in body["error"]
        assert "message" in body["error"]
