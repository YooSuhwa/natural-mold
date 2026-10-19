"""Reject tuples OpenFGA would refuse: relation, subject type restriction and condition."""

from functools import cache

from pydantic import JsonValue

from app.authz.model_document import model_document
from app.authz.tuple_types import RelationTuple, TupleRuleError

# Supplied per query by AuthzService (plan 7.3); never stored on a tuple.
RUNTIME_PARAMETERS = frozenset({"current_time", "run_source", "user_ip"})

_Restriction = tuple[str, str, str]  # (subject type, subject relation, condition)


def _mapping(value: JsonValue) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def _entries(value: JsonValue) -> list[dict[str, JsonValue]]:
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


@cache
def _restrictions() -> dict[tuple[str, str], frozenset[_Restriction]]:
    found: dict[tuple[str, str], frozenset[_Restriction]] = {}
    for definition in model_document().type_definitions:
        relations = _mapping(_mapping(definition.get("metadata")).get("relations"))
        for relation, spec in relations.items():
            allowed = _entries(_mapping(spec).get("directly_related_user_types"))
            found[(str(definition["type"]), relation)] = frozenset(
                (str(e["type"]), str(e.get("relation", "")), str(e.get("condition", "")))
                for e in allowed
            )
    return found


@cache
def _condition_parameters() -> dict[str, frozenset[str]]:
    return {
        name: frozenset(_mapping(_mapping(body).get("parameters")))
        for name, body in model_document().conditions.items()
    }


def assert_model_valid(item: RelationTuple) -> None:
    object_type = item.object.split(":", 1)[0]
    allowed = _restrictions().get((object_type, item.relation))
    if allowed is None:
        raise TupleRuleError(f"{object_type} has no directly assignable relation {item.relation}")
    subject, _, subject_relation = item.user.partition("#")
    condition = item.condition.name if item.condition else ""
    if (subject.split(":", 1)[0], subject_relation, condition) not in allowed:
        raise TupleRuleError(
            f"{item.user} is not assignable to {object_type}#{item.relation} [{condition}]"
        )
    if item.condition is not None:
        parameters = _condition_parameters()[condition]
        stored = frozenset(item.condition.context)
        if stored - parameters or parameters - stored - RUNTIME_PARAMETERS:
            raise TupleRuleError(f"{condition} context {sorted(stored)} != {sorted(parameters)}")
