from __future__ import annotations

import base64
import hashlib
from html import unescape
from pathlib import Path
from uuid import UUID

import pytest
from deepagents.backends.protocol import ReadResult
from deepagents.backends.utils import check_empty_content, slice_read_response
from langchain_core.messages import HumanMessage

from app.agent_runtime.history_archive_render import paged_archive_read
from app.agent_runtime.offload_storage import OffloadIdentity, ScopedOffloadBackend
from app.agent_runtime.offload_storage_backends import HistoryBackend

_PIXEL = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jvVEAAAAASUVORK5CYII="
)
_ROOT_ACTOR = UUID("11111111-1111-4111-8111-111111111111")


@pytest.fixture
def backend(tmp_path: Path) -> ScopedOffloadBackend:
    # Shared-agent chat has different physical and virtual history owners.
    return ScopedOffloadBackend(
        tmp_path,
        OffloadIdentity(owner_id="agent-owner", conversation_id="chat", run_id="turn"),
        _ROOT_ACTOR,
        archive_owner_id="conversation-owner",
    )


@pytest.mark.parametrize("stored", ["matching", "missing", "different", "malformed"])
def test_model_history_only_links_verified_uploaded_inline_media(
    backend: ScopedOffloadBackend, stored: str
) -> None:
    image = base64.b64decode(_PIXEL)
    payload = "Y" if stored == "malformed" else _PIXEL
    image_name = hashlib.sha256(image).hexdigest()[:16] + ".png"
    reference = f"{backend.artifacts_root}/conversation_history/media/{image_name}"
    if stored in {"matching", "different"}:
        uploaded = backend.upload_files(
            [(reference, image if stored == "matching" else b"a different image")]
        )
        assert len(uploaded) == 1 and uploaded[0].error is None

    message = HumanMessage(
        content=[
            {"type": "text", "text": "이 이미지를 확인해 주세요."},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{payload}"}},
        ],
        id="question",
    )
    archive = backend.archive_compaction("cp-image", [message])
    original = b"".join(backend.iter_archive_bytes(archive))
    assert original == (message.model_dump_json() + "\n").encode()

    download = backend.download_files([archive])[0]
    assert download.error is None and download.content is not None
    xml = unescape(download.content.decode())
    assert _PIXEL not in xml and "base64," not in xml
    if stored == "matching":
        assert f'<image url="{reference}"' in xml
        assert "failed_to_offload" not in xml
    else:
        assert '<image error="failed_to_offload" />' in xml
        assert reference not in xml
    read = backend.read(archive)
    assert read.file_data is not None
    assert unescape(read.file_data["content"]) == xml


def _expected_read(text: str, offset: int, limit: int) -> ReadResult:
    empty = check_empty_content(text)
    if empty:
        return ReadResult(file_data={"content": empty, "encoding": "utf-8"})
    return slice_read_response({"content": text, "encoding": "utf-8"}, offset, limit)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "\r\n\t \u2003",
        "처음\r\n둘째\r셋째\u2028넷째\x85다섯째\n",
        "\r\n가나\v다라\f마바\x1c사아\x1d자차\x1e카타\u2029파하\r",
        "한글🙂\n\n마지막",
    ],
)
@pytest.mark.parametrize(("offset", "limit"), [(-1, 2), (0, 0), (2, -1), (1, 3), (0, 99), (30, 4)])
def test_archive_page_matches_upstream_for_split_unicode_and_line_endings(
    text: str, offset: int, limit: int
) -> None:
    # Single-byte pieces split every multibyte code point and every CRLF.
    pieces = (bytes([value]) for value in text.encode())
    assert paged_archive_read(pieces, offset, limit) == _expected_read(text, offset, limit)


def test_reading_one_line_does_not_download_the_whole_history(
    backend: ScopedOffloadBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = backend.archive_compaction("first", [HumanMessage(id="a", content="하나\r\n둘\r셋")])
    archive = backend.archive_compaction(
        "second", [HumanMessage(id="b", content="넷\u2028다섯\n여섯")], previous_file_path=first
    )
    download = backend.download_files([archive])[0]
    assert download.error is None and download.content is not None
    expected = _expected_read(download.content.decode(), 3, 1)

    def reject_full_read(self: HistoryBackend, key: str, *, root: Path | None = None) -> bytes:
        pytest.fail(f"paged read unexpectedly requested the whole archive: {key}")

    monkeypatch.setattr(HistoryBackend, "_read_bytes", reject_full_read)
    assert backend.read(archive, offset=3, limit=1) == expected
