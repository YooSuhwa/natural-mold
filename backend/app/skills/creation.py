"""Explicit immutable inputs for scoped Skill writers."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TextSkillCreation:
    name: str
    slug: str | None
    description: str | None
    content: str
    version: str | None = None


@dataclass(frozen=True, slots=True)
class PackageSkillCreation:
    zip_bytes: bytes
    name_override: str | None = None
    slug_override: str | None = None
