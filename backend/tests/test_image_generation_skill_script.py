from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path


def _load_script_module():
    root = Path(__file__).resolve().parents[1]
    script_path = (
        root
        / "app"
        / "seed"
        / "system_skill_packages"
        / "image-generation"
        / "scripts"
        / "generate_image.py"
    )
    spec = importlib.util.spec_from_file_location(
        "moldy_image_generation_skill", script_path
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_generate_image_uses_images_generations_for_gpt_image_model(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_script_module()
    monkeypatch.setenv("IMAGE_API_BASE_URL", "https://image.example/v1")
    monkeypatch.setenv("IMAGE_API_KEY", "secret-key")

    calls: list[dict] = []

    def fake_post_json(url: str, headers: dict, payload: dict, timeout: float):
        calls.append(
            {
                "url": url,
                "headers": headers,
                "payload": payload,
                "timeout": timeout,
            }
        )
        return {
            "data": [
                {
                    "b64_json": "aW1hZ2UtYnl0ZXM=",
                }
            ],
            "output_format": "png",
        }

    monkeypatch.setattr(module, "_post_json", fake_post_json)

    result = module.generate_image(
        prompt="A small teal robot icon",
        output_dir=tmp_path,
        aspect_ratio="3:4",
        image_size="1K",
    )

    output_path = Path(result["output_path"])
    assert output_path.read_bytes() == b"image-bytes"
    assert output_path.suffix == ".png"
    assert calls[0]["url"] == "https://image.example/v1/images/generations"
    assert calls[0]["headers"]["Authorization"] == "Bearer secret-key"
    assert calls[0]["payload"]["model"] == "gpt-image-2"
    assert calls[0]["payload"]["prompt"] == "A small teal robot icon"
    assert calls[0]["payload"]["size"] == "1024x1536"
    assert "modalities" not in calls[0]["payload"]
    assert calls[0]["timeout"] == 360.0


def test_generate_image_saves_content_image_url_data_url(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_script_module()
    monkeypatch.setenv("IMAGE_API_BASE_URL", "https://image.example/v1/")
    monkeypatch.setenv("IMAGE_API_KEY", "secret-key")

    def fake_post_json(url: str, headers: dict, payload: dict, timeout: float):
        return {
            "choices": [
                {
                    "message": {
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/webp;base64,d2VicC1ieXRlcw=="
                                },
                            }
                        ],
                    }
                }
            ]
        }

    monkeypatch.setattr(module, "_post_json", fake_post_json)

    result = module.generate_image(
        prompt="A product photo",
        output_dir=tmp_path,
        aspect_ratio="16:9",
        image_size="1K",
        model="legacy-chat-image-model",
    )

    output_path = Path(result["output_path"])
    assert output_path.read_bytes() == b"webp-bytes"
    assert output_path.suffix == ".webp"


def test_generate_image_uses_openrouter_chat_completions_default(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_script_module()
    monkeypatch.setenv("IMAGE_API_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("IMAGE_API_KEY", "openrouter-key")

    calls: list[dict] = []

    def fake_post_json(url: str, headers: dict, payload: dict, timeout: float):
        calls.append(
            {
                "url": url,
                "headers": headers,
                "payload": payload,
                "timeout": timeout,
            }
        )
        return {
            "choices": [
                {
                    "message": {
                        "images": [
                            {
                                "image_url": {
                                    "url": "data:image/png;base64,b3BlbnJvdXRlci1ieXRlcw=="
                                }
                            }
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr(module, "_post_json", fake_post_json)

    result = module.generate_image(
        prompt="울산 관광 가이드맵을 만들어줘",
        output_dir=tmp_path,
        aspect_ratio="3:4",
        image_size="1K",
    )

    output_path = Path(result["output_path"])
    assert output_path.read_bytes() == b"openrouter-bytes"
    assert output_path.suffix == ".png"
    assert result["model"] == "openai/gpt-5.4-image-2"
    assert result["provider_profile"] == "openrouter"
    assert calls[0]["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer openrouter-key"
    assert calls[0]["payload"]["model"] == "openai/gpt-5.4-image-2"
    assert calls[0]["payload"]["modalities"] == ["image", "text"]
    assert calls[0]["payload"]["messages"] == [
        {"role": "user", "content": result["prompt"]}
    ]
    assert "image_config" not in calls[0]["payload"]


def test_generate_image_model_env_overrides_provider_default(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_script_module()
    monkeypatch.setenv("IMAGE_API_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("IMAGE_API_KEY", "openrouter-key")
    monkeypatch.setenv("IMAGE_MODEL", "openrouter/custom-image-model")

    calls: list[dict] = []

    def fake_post_json(url: str, headers: dict, payload: dict, timeout: float):
        calls.append({"url": url, "payload": payload})
        return {
            "choices": [
                {
                    "message": {
                        "images": [
                            {
                                "image_url": {
                                    "url": "data:image/png;base64,Y3VzdG9tLWJ5dGVz"
                                }
                            }
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr(module, "_post_json", fake_post_json)

    result = module.generate_image(
        prompt="A small icon",
        output_dir=tmp_path,
    )

    assert result["model"] == "openrouter/custom-image-model"
    assert calls[0]["payload"]["model"] == "openrouter/custom-image-model"
    assert calls[0]["url"] == "https://openrouter.ai/api/v1/chat/completions"


def test_post_json_retries_transient_gateway_timeout(monkeypatch) -> None:
    module = _load_script_module()
    attempts: list[float] = []
    sleeps: list[float] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self) -> bytes:
            return b'{"ok": true}'

    def fake_urlopen(req, timeout: float):
        attempts.append(timeout)
        if len(attempts) == 1:
            raise module.urllib.error.HTTPError(
                req.full_url,
                504,
                "Gateway Timeout",
                {},
                io.BytesIO(b"<HTML><H1>504 Gateway Timeout ERROR</H1></HTML>"),
            )
        return FakeResponse()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = module._post_json(
        "https://image.example/v1/images/generations",
        headers={},
        payload={"model": "gpt-image-2"},
        timeout=240.0,
    )

    assert result == {"ok": True}
    assert len(attempts) == 2
    assert sleeps == [1.0]


def test_post_json_shortens_html_gateway_timeout_errors(monkeypatch) -> None:
    module = _load_script_module()

    def fake_urlopen(req, timeout: float):
        raise module.urllib.error.HTTPError(
            req.full_url,
            504,
            "Gateway Timeout",
            {},
            io.BytesIO(
                b'<!DOCTYPE HTML><HTML><HEAD><TITLE>ERROR: The request could not be '
                b"satisfied</TITLE></HEAD><BODY><H1>504 Gateway Timeout ERROR</H1>"
                b"<PRE>Request ID: abc123</PRE></BODY></HTML>"
            ),
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    try:
        module._post_json(
            "https://image.example/v1/images/generations",
            headers={},
            payload={"model": "gpt-image-2"},
            timeout=240.0,
            max_attempts=1,
        )
    except module.SkillError as exc:
        assert exc.code == "TRANSIENT_API_ERROR"
        assert "HTTP 504 Gateway Timeout" in exc.message
        assert "Request ID: abc123" in exc.message
        assert "<HTML>" not in exc.message
        assert "<H1>" not in exc.message
    else:
        raise AssertionError("expected SkillError")


def test_post_json_retries_read_timeout_and_returns_json_error(monkeypatch) -> None:
    module = _load_script_module()
    attempts: list[float] = []
    sleeps: list[float] = []

    def fake_urlopen(req, timeout: float):
        attempts.append(timeout)
        raise TimeoutError("The read operation timed out")

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: sleeps.append(seconds))

    try:
        module._post_json(
            "https://image.example/v1/images/generations",
            headers={},
            payload={"model": "gpt-image-2"},
            timeout=240.0,
        )
    except module.SkillError as exc:
        assert exc.code == "TRANSIENT_API_ERROR"
        assert "timed out" in exc.message
        assert "after 3 attempts" in exc.message
    else:
        raise AssertionError("expected SkillError")

    assert len(attempts) == 3
    assert sleeps == [1.0, 2.0]


def test_prepare_prompt_formats_travel_guide_requests() -> None:
    module = _load_script_module()

    prompt = module.prepare_prompt("후쿠오카 주말 여행 가이드 이미지 만들어줘")

    assert prompt.startswith(
        "[후쿠오카] 관광 가이드맵을 미니멀한 라인 아트 캐릭터를 사용한 "
        "모던한 에디토리얼 일러스트레이션으로 만들어 줘."
    )
    assert "대표 랜드마크" in prompt
    assert "주말 여행" in prompt


def test_prepare_prompt_rewrites_english_travel_guide_to_korean() -> None:
    module = _load_script_module()

    prompt = module.prepare_prompt(
        "Ulsan South Korea tourism guide map, modern editorial illustration "
        "with minimal line art characters, Daewangam Park, Jangsaengpo Whale "
        "Museum, Taehwa River bamboo forest, local seafood, travel routes"
    )

    assert prompt.startswith(
        "[울산] 관광 가이드맵을 미니멀한 라인 아트 캐릭터를 사용한 "
        "모던한 에디토리얼 일러스트레이션으로 만들어 줘."
    )
    assert "대표 랜드마크" in prompt
    assert "한글 라벨" in prompt
    assert "영어 문구" in prompt
