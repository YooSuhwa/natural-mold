"""Tuple value types shared by ``tuples``, its model guard and its merge step (plan 7.2)."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from pydantic import JsonValue

RUN_SOURCES: Final = ("CHAT", "TRIGGER", "CHANNEL", "SUBAGENT", "API")
EXPIRY_CONDITION: Final = "non_expired_grant"
RUN_SOURCE_CONDITION: Final = "allowed_run_source"


class TupleRuleError(ValueError):
    """A source row that no 7.2 rule can project (would be rejected by the model)."""


@dataclass(frozen=True, slots=True)
class Condition:
    name: str
    context_json: str = "{}"

    @property
    def context(self) -> dict[str, JsonValue]:
        return json.loads(self.context_json)


@dataclass(frozen=True, slots=True)
class RelationTuple:
    user: str
    relation: str
    object: str
    condition: Condition | None = None

    @classmethod
    def of(
        cls,
        user: str,
        relation: str,
        obj: str,
        condition: str | None = None,
        context: Mapping[str, JsonValue] | None = None,
    ) -> "RelationTuple":
        if condition is None:
            return cls(user, relation, obj)
        encoded = json.dumps(dict(context or {}), sort_keys=True, separators=(",", ":"))
        return cls(user, relation, obj, Condition(condition, encoded))

    @property
    def sort_key(self) -> tuple[str, str, str, str, str]:
        condition = self.condition or Condition("", "")
        return (self.object, self.relation, self.user, condition.name, condition.context_json)
