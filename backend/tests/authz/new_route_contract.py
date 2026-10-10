"""Extract the new API schema and its component-reference closure."""

from pathlib import Path

from fastapi.routing import APIRoute
from pydantic import JsonValue, TypeAdapter

from app.main import create_app

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
    app = create_app()
    document = DOCUMENT.validate_python(app.openapi())
    paths, components = document["paths"], document["components"]
    assert isinstance(paths, dict) and isinstance(components, dict)
    schemas = components["schemas"]
    assert isinstance(schemas, dict)
    legacy = set(
        (Path(__file__).resolve().parents[2] / "app/authz/legacy_routes.txt")
        .read_text()
        .splitlines()
    )
    selected: dict[str, JsonValue] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path.startswith("/api/e2e/"):
            continue
        methods = {method for method in route.methods if f"{method} {route.path}" not in legacy}
        if not methods:
            continue
        assert route.include_in_schema, f"New route is missing from OpenAPI: {route.path}"
        operations = paths[route.path_format]
        assert isinstance(operations, dict)
        included_operations: dict[str, JsonValue] = {
            method.lower(): operations[method.lower()] for method in methods
        }
        if included_operations:
            selected.setdefault(route.path_format, {})
            route_operations = selected[route.path_format]
            assert isinstance(route_operations, dict)
            route_operations.update(included_operations)
    included: dict[str, JsonValue] = {}
    pending = references(selected)
    while pending:
        name = pending.pop()
        included[name] = schemas[name]
        pending.update(references(schemas[name]) - included.keys())
    return {"paths": selected, "schemas": included}
