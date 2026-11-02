"""Chat attachments as model input (feature plan v2 1A).

The user's HumanMessage carries only light references in
``additional_kwargs["moldy_attachments"]`` (id, name, mime, form). The checkpoint
therefore never stores file bytes. ``AttachmentInputMiddleware`` expands the
references into content blocks for each model call:

- ``image``: a standard base64 image block, when the run's model supports vision;
- ``text``: the decoded text (``text/*`` and JSON), capped;
- anything else, or an image for a model without vision: a short text note, so
  the model can tell the user what it could not see.

The form is decided once per run from the mime type and the run's model
(``attachment_form``) and recorded on the run. Each model call re-validates it:
the row must still belong to this conversation, the file must exist, and an
image is only sent while the current model still supports vision.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import AnyMessage, BaseMessage, HumanMessage

logger = logging.getLogger(__name__)

ATTACHMENT_REFS_KEY = "moldy_attachments"

AttachmentForm = Literal["image", "image_no_vision", "text", "unsupported"]

# Provider image limits differ (Anthropic 5 MB, OpenAI 20 MB); stay under the lowest.
MAX_IMAGE_BYTES = 3_750_000
MAX_IMAGE_SIDE = 1568
# Older images are replaced by a note so a long conversation stays under provider caps.
MAX_IMAGES_PER_CALL = 8
MAX_TEXT_CHARS = 50_000
_PROVIDER_IMAGE_MIMES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})


@dataclass(frozen=True, slots=True)
class AttachmentFile:
    """A conversation attachment the run may read, resolved before the graph runs."""

    id: str
    filename: str
    mime_type: str
    path: Path


@dataclass(frozen=True, slots=True)
class AttachmentInputs:
    """What the attachment middleware of one run may read."""

    files: Mapping[str, AttachmentFile]
    supports_vision: bool | None


def _base_mime(mime: str) -> str:
    return mime.split(";", 1)[0].strip().lower()


def attachment_form(mime_type: str, *, supports_vision: bool | None) -> AttachmentForm:
    """How an attachment reaches the model. Unknown vision support counts as none:
    an image sent to a text-only model fails the call and every later turn."""

    base = _base_mime(mime_type)
    if base.startswith("image/") and base != "image/svg+xml":
        return "image" if supports_vision is True else "image_no_vision"
    if base.startswith("text/") or base == "application/json":
        return "text"
    return "unsupported"


def attachment_ref(
    *, attachment_id: str, filename: str, mime_type: str, form: AttachmentForm
) -> dict[str, str]:
    return {"id": attachment_id, "filename": filename, "mime_type": mime_type, "form": form}


def _strip_refs(message: Any) -> Any:
    if isinstance(message, BaseMessage):
        if ATTACHMENT_REFS_KEY not in message.additional_kwargs:
            return message
        kwargs = dict(message.additional_kwargs)
        del kwargs[ATTACHMENT_REFS_KEY]
        return message.model_copy(update={"additional_kwargs": kwargs})
    if isinstance(message, Mapping):
        cleaned = {key: value for key, value in message.items() if key != ATTACHMENT_REFS_KEY}
        nested = cleaned.get("additional_kwargs")
        if isinstance(nested, Mapping) and ATTACHMENT_REFS_KEY in nested:
            cleaned["additional_kwargs"] = {
                key: value for key, value in nested.items() if key != ATTACHMENT_REFS_KEY
            }
        return cleaned
    return message


def _is_human(message: Any) -> bool:
    if isinstance(message, BaseMessage):
        return message.type == "human"
    if isinstance(message, Mapping):
        return (message.get("role") or message.get("type")) in {"human", "user"}
    return False


def _with_refs(message: Any, refs: list[dict[str, str]]) -> Any:
    if isinstance(message, BaseMessage):
        kwargs = {**message.additional_kwargs, ATTACHMENT_REFS_KEY: refs}
        return message.model_copy(update={"additional_kwargs": kwargs})
    nested = dict(message.get("additional_kwargs") or {})
    nested[ATTACHMENT_REFS_KEY] = refs
    return {**message, "additional_kwargs": nested}


def tag_input_with_attachments(payload: Any, refs: list[dict[str, str]]) -> Any:
    """Put ``refs`` on the input's last human message; drop client-supplied refs.

    ``payload`` is either a LangGraph input dict (``{"messages": [...]}``) or the
    legacy list of ``{"role", "content"}`` messages. Only the server writes refs.
    """

    messages = payload.get("messages") if isinstance(payload, Mapping) else payload
    if not isinstance(messages, list):
        return payload
    tagged = [_strip_refs(message) for message in messages]
    if refs:
        for index in range(len(tagged) - 1, -1, -1):
            if _is_human(tagged[index]):
                tagged[index] = _with_refs(tagged[index], refs)
                break
    if isinstance(payload, Mapping):
        return {**payload, "messages": tagged}
    return tagged


def _note(ref: Mapping[str, Any], reason: str) -> dict[str, str]:
    return {"type": "text", "text": f"[첨부 파일: {ref.get('filename') or '이름 없음'} — {reason}]"}


def _read_text(path: Path) -> tuple[str, bool]:
    with path.open("rb") as fh:
        raw = fh.read(MAX_TEXT_CHARS * 4 + 4)
    text = raw.decode("utf-8", errors="replace")
    truncated = len(text) > MAX_TEXT_CHARS or path.stat().st_size > len(raw)
    return text[:MAX_TEXT_CHARS], truncated


def _read_image(path: Path, mime_type: str) -> tuple[bytes, str] | None:
    data = path.read_bytes()
    base = _base_mime(mime_type)
    if base in _PROVIDER_IMAGE_MIMES and len(data) <= MAX_IMAGE_BYTES:
        return data, base
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
            keeps_alpha = image.mode in {"RGBA", "LA", "P"}
            converted = image.convert("RGBA" if keeps_alpha else "RGB")
            out = io.BytesIO()
            if keeps_alpha:
                converted.save(out, format="PNG", optimize=True)
            else:
                converted.save(out, format="JPEG", quality=85)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    encoded = out.getvalue()
    if len(encoded) > MAX_IMAGE_BYTES:
        return None
    return encoded, "image/png" if keeps_alpha else "image/jpeg"


def _refs_of(message: BaseMessage) -> list[Mapping[str, Any]]:
    if message.type != "human":
        return []
    refs = message.additional_kwargs.get(ATTACHMENT_REFS_KEY)
    if not isinstance(refs, list):
        return []
    return [ref for ref in refs if isinstance(ref, Mapping) and isinstance(ref.get("id"), str)]


def _content_blocks(content: Any) -> list[Any]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if isinstance(content, list):
        return list(content)
    return [{"type": "text", "text": str(content)}]


class AttachmentInputMiddleware(AgentMiddleware):
    """Expand attachment references into model content blocks for each call."""

    def __init__(self, inputs: AttachmentInputs):
        super().__init__()
        self.tools = []
        self._files = dict(inputs.files)
        self._supports_vision = inputs.supports_vision
        self._blocks: dict[str, dict[str, Any]] = {}

    def _block(self, ref: Mapping[str, Any], *, image_allowed: bool) -> dict[str, Any]:
        attachment = self._files.get(str(ref["id"]))
        form = ref.get("form")
        if attachment is None:
            return _note(ref, "파일을 찾을 수 없어 전달하지 못했습니다")
        if form == "image_no_vision" or (form == "image" and self._supports_vision is not True):
            return _note(ref, "현재 모델이 이미지를 지원하지 않아 내용을 전달하지 못했습니다")
        if form == "image" and not image_allowed:
            return _note(ref, f"이미지가 {MAX_IMAGES_PER_CALL}개를 넘어 이전 이미지는 생략했습니다")
        if form not in {"image", "text"}:
            return _note(ref, f"{attachment.mime_type} 형식은 아직 모델에 전달하지 않습니다")
        cached = self._blocks.get(attachment.id)
        if cached is not None:
            return cached
        block = self._load(attachment, form)
        self._blocks[attachment.id] = block
        return block

    def _load(self, attachment: AttachmentFile, form: str) -> dict[str, Any]:
        ref = {"filename": attachment.filename}
        try:
            if form == "text":
                text, truncated = _read_text(attachment.path)
                suffix = f"\n(앞부분 {MAX_TEXT_CHARS}자만 포함)" if truncated else ""
                return {
                    "type": "text",
                    "text": f"[첨부 파일: {attachment.filename}]\n```\n{text}\n```{suffix}",
                }
            image = _read_image(attachment.path, attachment.mime_type)
        except OSError:
            logger.warning("attachment read failed id=%s", attachment.id, exc_info=True)
            return _note(ref, "파일을 읽지 못해 전달하지 못했습니다")
        if image is None:
            return _note(ref, "이미지를 변환하지 못해 전달하지 못했습니다")
        data, mime_type = image
        return {
            "type": "image",
            "base64": base64.b64encode(data).decode("ascii"),
            "mime_type": mime_type,
        }

    def _expand(self, messages: Sequence[AnyMessage]) -> list[AnyMessage] | None:
        tagged = [(index, _refs_of(message)) for index, message in enumerate(messages)]
        tagged = [(index, refs) for index, refs in tagged if refs]
        if not tagged:
            return None
        image_ids = [
            str(ref["id"]) for _, refs in tagged for ref in refs if ref.get("form") == "image"
        ]
        allowed_images = set(image_ids[-MAX_IMAGES_PER_CALL:])
        expanded = list(messages)
        for index, refs in tagged:
            message = messages[index]
            blocks = _content_blocks(message.content)
            blocks += [
                self._block(ref, image_allowed=str(ref["id"]) in allowed_images) for ref in refs
            ]
            # The provider gets the blocks, not Moldy's reference key.
            expanded[index] = HumanMessage(content=blocks, id=message.id, name=message.name)
        return expanded

    def wrap_model_call(self, request: ModelRequest, handler: Any) -> Any:
        expanded = self._expand(request.messages)
        return handler(request if expanded is None else request.override(messages=expanded))

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> Any:
        expanded = await asyncio.to_thread(self._expand, request.messages)
        return await handler(request if expanded is None else request.override(messages=expanded))


def attachment_manifest(refs: Iterable[Mapping[str, str]], *, supports_vision: bool | None) -> dict:
    """The run record of what this input's attachments became for the model."""

    return {
        "supports_vision": supports_vision,
        "items": [dict(ref) for ref in refs],
    }
