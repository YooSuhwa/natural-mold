from app.agent_runtime.stream_error_messages import public_stream_error_message

SAFE_MODEL_ERROR = (
    "모델 제공자 요청이 실패했습니다. 모델 설정, 자격증명, 사용량 한도를 확인해주세요."
)


def test_public_stream_error_message_hides_provider_budget_details() -> None:
    message = public_stream_error_message(
        RuntimeError(
            "Error code: 400 - {'error': {'message': 'Budget has been exceeded! "
            "Team=c23c3394-3735-43bd-a00d-e20e1088f536 Current cost: 200.31, "
            "Max budget: 200.0', 'type': 'budget_exceeded'}}"
        )
    )

    assert message == SAFE_MODEL_ERROR
    assert "Team=" not in message
    assert "200.31" not in message


def test_public_stream_error_message_preserves_plain_application_errors() -> None:
    assert public_stream_error_message(RuntimeError("도구 실행에 실패했습니다.")) == (
        "도구 실행에 실패했습니다."
    )
