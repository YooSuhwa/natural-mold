"""Model-facing views of immutable private chat archives."""

from __future__ import annotations

import codecs
import hashlib
from collections.abc import Callable, Iterable

from deepagents.backends.protocol import ReadResult
from deepagents.backends.utils import EMPTY_CONTENT_WARNING, normalize_read_bounds
from deepagents.middleware.summarization import (
    _decode_data_url,
    _extract_data_url,
    _rewrite_data_url_blocks,
)
from langchain_core.messages import AnyMessage, get_buffer_string

# str.splitlines treats these as line endings in addition to CR and LF.
_LINE_ENDS = ("\r", "\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")


def render_archive_message(
    message: AnyMessage,
    *,
    history_prefix: str,
    read_media: Callable[[str], bytes],
) -> bytes:
    """Render only verified, actor-scoped references for inline media in the original."""
    if not isinstance(message.content, str):
        paths: dict[str, str] = {}
        saw_media = False
        for block in message.content_blocks:
            data_url = _extract_data_url(block)
            if data_url is None:
                continue
            saw_media = True
            decoded = _decode_data_url(data_url)
            if decoded is None:
                continue
            original, ext, _mime = decoded
            digest = hashlib.sha256(original).hexdigest()[:16]
            name = f"media/{digest}.{ext}"
            # Blob names use a shortened digest in the Deep Agents media contract.
            # Verify the actual bytes before linking a historical attachment.
            try:
                saved = read_media(name)
            except (OSError, ValueError):
                continue
            if saved == original:
                paths[digest] = f"{history_prefix}/{name}"
        if saw_media:
            projected, _failures = _rewrite_data_url_blocks([message], paths)
            message = projected[0]
    return get_buffer_string([message], format="xml").encode("utf-8")


def paged_archive_read(pieces: Iterable[bytes], offset: int, limit: int) -> ReadResult:
    """Keep only a requested splitlines window while verifying all streamed records."""
    offset, limit = normalize_read_bounds(offset, limit)
    selected: list[str] = []
    total = 0
    nonblank = False
    trailing_line = False
    previous_cr = False
    for text in codecs.iterdecode(pieces, "utf-8"):
        if not text:
            continue
        if previous_cr and text.startswith("\n"):
            text = text[1:]
        previous_cr = text.endswith("\r")
        if not text:
            continue
        nonblank |= not text.isspace()
        for fragment in text.splitlines(keepends=True):
            if offset <= total < offset + limit:
                selected.append(fragment.replace("\r\n", "\n").replace("\r", "\n"))
            trailing_line = not fragment.endswith(_LINE_ENDS)
            if not trailing_line:
                total += 1
    total += int(trailing_line)
    if not nonblank:
        return ReadResult(file_data={"encoding": "utf-8", "content": EMPTY_CONTENT_WARNING})
    if limit == 0:
        return ReadResult(file_data={"encoding": "utf-8", "content": ""}, no_lines_requested=True)
    if offset >= total:
        return ReadResult(error=f"Line offset {offset} exceeds file length ({total} lines)")
    end = min(total, offset + limit)
    return ReadResult(
        file_data={"encoding": "utf-8", "content": "".join(selected)},
        total_lines=total,
        start_line=offset + 1,
        end_line=end,
        next_offset=end if end < total else None,
    )
