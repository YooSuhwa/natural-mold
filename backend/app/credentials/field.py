"""Field definitions for credential / tool dynamic forms.

Mirrors the information structure of a generic node-parameter system:
``FieldKind`` is the renderer hint, ``FieldDef`` carries display + validation
metadata. ``display_options.show`` enables conditional rendering driven by
sibling field values.

Algorithm/structure attribution: see NOTICES.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class FieldKind(StrEnum):
    """Rendering hint for the front-end ``DynamicFieldsForm``.

    Values are stable identifiers used by both the JSON serialization of a
    :class:`CredentialDefinition` and the React renderer.
    """

    STRING = "string"
    PASSWORD = "password"
    NUMBER = "number"
    SELECT = "select"
    MULTILINE = "multiline"
    JSON = "json"
    OAUTH_BUTTON = "oauth_button"
    TOGGLE = "toggle"
    COLLECTION = "collection"


@dataclass
class FieldDef:
    """A single form field on a credential or tool."""

    name: str
    display_name: str
    kind: FieldKind = FieldKind.STRING
    default: Any = None
    required: bool = False
    description: str | None = None
    options: list[dict[str, Any]] = field(default_factory=list)
    """For ``SELECT`` kind: list of ``{"name": "Label", "value": "v"}`` entries."""

    placeholder: str | None = None

    type_options: dict[str, Any] = field(default_factory=dict)
    """Renderer-specific switches: ``password``, ``multiline``, ``rows``,
    ``min``, ``max``, ``regex``, ``expirable`` (token TTL), etc."""

    display_options: dict[str, Any] = field(default_factory=dict)
    """Conditional display. ``show`` is a dict mapping a sibling field name to
    the list of values that make this field visible. e.g.
    ``{"show": {"auth_type": ["bearer", "basic"]}}``."""

    def serialize(self) -> dict[str, Any]:
        """JSON-friendly representation for the API catalog endpoint."""

        return {
            "name": self.name,
            "display_name": self.display_name,
            "kind": self.kind.value,
            "default": self.default,
            "required": self.required,
            "description": self.description,
            "options": list(self.options),
            "placeholder": self.placeholder,
            "type_options": dict(self.type_options),
            "display_options": dict(self.display_options),
        }


__all__ = ["FieldDef", "FieldKind"]
