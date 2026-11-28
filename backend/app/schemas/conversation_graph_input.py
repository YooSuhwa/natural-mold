"""Graph inputs accepted from chat clients."""

from typing import Annotated, Final, TypeVar

from pydantic import AfterValidator

OMITTED_MEDIA_KEY: Final = "moldy_omitted_media"
STEER_INPUT_MARKER: Final = "moldy_steer_input"
STEER_RECEIPTS_CHANNEL: Final = "moldy_run_steer_receipts"
CHILD_RECEIPTS_CHANNEL: Final = "moldy_child_model_receipts"
PRIVATE_STEER_CHANNELS: Final[frozenset[str]] = frozenset(
    (STEER_RECEIPTS_CHANNEL, CHILD_RECEIPTS_CHANNEL)
)


def is_omitted_user_message(message: object) -> bool:
    if not isinstance(message, dict) or message.get("role") not in ("user", "human"):
        return False
    metadata = message.get("additional_kwargs")
    return (
        message.get("content") == ""
        and isinstance(metadata, dict)
        and metadata.keys() == {OMITTED_MEDIA_KEY}
        and metadata[OMITTED_MEDIA_KEY] is True
    )


def _check_public_channels[T](values: dict[str, T]) -> dict[str, T]:
    if not PRIVATE_STEER_CHANNELS.isdisjoint(values):
        raise ValueError("Steer delivery receipts are managed by the runtime")
    # LangChain accepts flat message dicts, additional_kwargs, and LC constructor
    # envelopes. Reserve this metadata key throughout the graph-message channel.
    pending: list[object] = [values.get("messages")]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if OMITTED_MEDIA_KEY in item:
                raise ValueError("Media omission metadata is managed by the runtime")
            # Content blocks are data. Only message/envelope fields can mint
            # the runtime's additional_kwargs, including flattened LC fields.
            pending.extend(value for key, value in item.items() if key != "content")
        elif isinstance(item, list):
            pending.extend(item)
    return values


# Keep the existing object on the wire and in OpenAPI; a named type alias adds a $ref.
_Value = TypeVar("_Value")
PublicGraphInput = Annotated[dict[str, _Value], AfterValidator(_check_public_channels)]
