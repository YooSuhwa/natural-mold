"""Load the canonical compiled FGA model through a typed JSON boundary."""

from hashlib import sha256
from pathlib import Path

from openfga_sdk.models.write_authorization_model_request import WriteAuthorizationModelRequest
from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ModelDocument(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: str
    type_definitions: list[dict[str, JsonValue]]
    conditions: dict[str, JsonValue] = Field(default_factory=dict)


def model_document() -> ModelDocument:
    return ModelDocument.model_validate_json(Path(__file__).with_name("model.json").read_text())


def sdk_model() -> WriteAuthorizationModelRequest:
    document = model_document()
    return WriteAuthorizationModelRequest(
        schema_version=document.schema_version,
        type_definitions=document.type_definitions,
        conditions=document.conditions,
    )


def model_sha256() -> str:
    return sha256(Path(__file__).with_name("model.json").read_bytes()).hexdigest()
