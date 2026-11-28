from app.agent_runtime.run_error_codes import run_error_code
from app.agent_runtime.stream_error_messages import public_stream_error_message
from app.agent_runtime.stream_retry_policy import PartialModelStreamError

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


def test_partial_stream_failure_preserves_public_code_without_gateway_details() -> None:
    failure = PartialModelStreamError()
    failure.__cause__ = RuntimeError("untrusted gateway diagnostic")
    assert run_error_code(failure, "stream_error") == "partial_model_stream"
    assert "gateway" not in public_stream_error_message(failure)
    outer = RuntimeError("provider failed")
    outer.__cause__ = failure
    assert run_error_code(outer, "runtime_error") == "partial_model_stream"
