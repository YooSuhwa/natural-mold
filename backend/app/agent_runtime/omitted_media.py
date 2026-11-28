"""Render an unavailable media-only turn for the model without changing chat history."""

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import HumanMessage

from app.schemas.conversation_graph_input import OMITTED_MEDIA_KEY

OMITTED_MEDIA_NOTE = "[첨부 파일: 지원하지 않는 미디어 형식이라 내용을 전달하지 못했습니다.]"


class OmittedMediaMiddleware(AgentMiddleware):
    @staticmethod
    def _model_request(request: ModelRequest) -> ModelRequest:
        messages = request.messages
        for index, message in enumerate(request.messages):
            if (
                isinstance(message, HumanMessage)
                and message.content == ""
                and message.additional_kwargs.get(OMITTED_MEDIA_KEY) is True
            ):
                if messages is request.messages:
                    messages = list(request.messages)
                metadata = dict(message.additional_kwargs)
                del metadata[OMITTED_MEDIA_KEY]
                messages[index] = message.model_copy(
                    update={"content": OMITTED_MEDIA_NOTE, "additional_kwargs": metadata}
                )
        return request if messages is request.messages else request.override(messages=messages)

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        return handler(self._model_request(request))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        return await handler(self._model_request(request))
