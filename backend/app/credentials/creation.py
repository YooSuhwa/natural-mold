"""Typed creation input shared by personal and explicit platform credential writers."""

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import JsonValue


@dataclass(frozen=True, slots=True)
class CredentialCreation:
    definition_key: str
    name: str
    data: Mapping[str, JsonValue]
    is_shared: bool = False
    source: str = "api"
