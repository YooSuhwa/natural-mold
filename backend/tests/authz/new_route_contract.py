"""Extract the new API schema and its component-reference closure."""

from pydantic import JsonValue, TypeAdapter

from app.main import create_app

NEW_PATHS = (
    "/api/me/organizations",
    "/api/me/active-org",
    "/api/me/installation",
)
DOCUMENT = TypeAdapter(dict[str, JsonValue])


def references(value: JsonValue) -> set[str]:
    match value:
        case dict():
            found = {
                reference.removeprefix("#/components/schemas/")
                for key, reference in value.items()
                if key == "$ref"
                and isinstance(reference, str)
                and reference.startswith("#/components/schemas/")
            }
            for child in value.values():
                found.update(references(child))
            return found
        case list():
            return set().union(*(references(child) for child in value))
        case _:
            return set()


def new_route_contract() -> dict[str, JsonValue]:
    document = DOCUMENT.validate_python(create_app().openapi())
    paths, components = document["paths"], document["components"]
    assert isinstance(paths, dict) and isinstance(components, dict)
    schemas = components["schemas"]
    assert isinstance(schemas, dict)
    selected: dict[str, JsonValue] = {path: paths[path] for path in NEW_PATHS}
    included: dict[str, JsonValue] = {}
    pending = references(selected)
    while pending:
        name = pending.pop()
        included[name] = schemas[name]
        pending.update(references(schemas[name]) - included.keys())
    return {"paths": selected, "schemas": included}
