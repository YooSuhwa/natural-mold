"""Graph inputs accepted from chat clients."""

from typing import Annotated, Final, TypeVar

from pydantic import AfterValidator

STEER_INPUT_MARKER: Final = "moldy_steer_input"
STEER_RECEIPTS_CHANNEL: Final = "moldy_run_steer_receipts"
CHILD_RECEIPTS_CHANNEL: Final = "moldy_child_model_receipts"
PRIVATE_STEER_CHANNELS: Final[frozenset[str]] = frozenset(
    (STEER_RECEIPTS_CHANNEL, CHILD_RECEIPTS_CHANNEL)
)


def _check_public_channels[T](values: dict[str, T]) -> dict[str, T]:
    if not PRIVATE_STEER_CHANNELS.isdisjoint(values):
        raise ValueError("Steer delivery receipts are managed by the runtime")
    return values


# Keep the existing object on the wire and in OpenAPI; a named type alias adds a $ref.
_Value = TypeVar("_Value")
PublicGraphInput = Annotated[dict[str, _Value], AfterValidator(_check_public_channels)]
