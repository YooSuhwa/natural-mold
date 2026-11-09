"""The ``moldy.workflow.v1`` definition (workflow-plan 3.2): shape only.

A draft must always parse (the editor saves partial work), so this module checks the
shape: node kinds, fields, identifiers, bounded sizes. Whether the graph can run (one
start, no cycles, every branch wired, references that exist upstream, usable agents) is
``checks.py``, run on demand and before publishing.

Templates are stored as segment lists, never as ``{{...}}`` strings: a ``ref`` names a
node id (or ``input``) and a JSON pointer, so renaming a node never breaks a reference
and the editor, AI edits and the checks read the same structure.
"""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from app.agent_runtime.structured_output import OutputSchemaError, check_output_schema

SCHEMA_VERSION: Final = "moldy.workflow.v1"
INPUT_REF: Final = "input"
MAX_NODES: Final = 50
MAX_EDGES: Final = 200
MAX_INPUTS: Final = 20
MAX_SEGMENTS: Final = 100
MAX_TEXT: Final = 8_000
MAX_APPROVAL_MINUTES: Final = 7 * 24 * 60

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")]
Pointer = Annotated[str, Field(pattern=r"^(/[^/]{1,64}){0,8}$")]
Label = Annotated[str, Field(max_length=80)]
Operator = Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "exists"]
EdgeWhen = Literal["true", "false", "approved", "rejected", "limit"]
MAX_ROUNDS: Final = 10


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Ref(_Strict):
    node: Identifier
    path: Pointer = ""
    # A loop (workflow-loop-plan 3.1): the previous round's value of a later step in the
    # same loop; empty in the first round. Stored only when set.
    previous: bool = False

    @model_serializer(mode="wrap")
    def _omit_default(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        if not self.previous:
            data.pop("previous", None)
        return data


class TextSegment(_Strict):
    text: str = Field(max_length=MAX_TEXT)


class RefSegment(_Strict):
    ref: Ref


Segment = TextSegment | RefSegment
Template = Annotated[list[Segment], Field(max_length=MAX_SEGMENTS)]


class WorkflowInput(_Strict):
    name: Identifier
    type: Literal["string", "number", "integer", "boolean"] = "string"
    required: bool = True
    label: Label | None = None
    max_length: int | None = Field(default=None, ge=1, le=100_000)

    @model_validator(mode="after")
    def _length_is_for_text(self) -> WorkflowInput:
        if self.max_length is not None and self.type != "string":
            raise ValueError("max_length is only for string inputs")
        return self


class Clause(_Strict):
    source: Identifier
    path: Pointer = ""
    op: Operator
    value: str | int | float | bool | None = None

    @model_validator(mode="after")
    def _value_matches_op(self) -> Clause:
        if self.op == "exists" and self.value is not None:
            raise ValueError("exists takes no value")
        if self.op in ("gt", "gte", "lt", "lte") and (
            isinstance(self.value, bool) or not isinstance(self.value, int | float)
        ):
            raise ValueError(f"{self.op} needs a number")
        if self.op == "contains" and not isinstance(self.value, str):
            raise ValueError("contains needs text")
        return self


class Condition(_Strict):
    all: Annotated[list[Clause], Field(min_length=1, max_length=20)] | None = None
    any: Annotated[list[Clause], Field(min_length=1, max_length=20)] | None = None

    @model_validator(mode="after")
    def _one_group(self) -> Condition:
        if (self.all is None) == (self.any is None):
            raise ValueError("a condition is either all or any")
        return self

    @property
    def clauses(self) -> list[Clause]:
        return self.all or self.any or []


class _Node(_Strict):
    id: Identifier
    label: Label | None = None

    @field_validator("id")
    @classmethod
    def _not_reserved(cls, value: str) -> str:
        if value == INPUT_REF:
            raise ValueError(f"{INPUT_REF!r} is reserved for workflow inputs")
        return value


class StartNode(_Node):
    type: Literal["start"]


class AgentNode(_Node):
    type: Literal["agent"]
    agent_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    prompt: Template
    output_schema: dict[str, Any]

    @field_validator("output_schema")
    @classmethod
    def _schema_in_subset(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            return check_output_schema(value)
        except OutputSchemaError as exc:
            raise ValueError(exc.message) from exc


class Repeat(_Strict):
    """How often a loop through this decision may run (rounds include the first)."""

    max_rounds: int = Field(ge=1, le=MAX_ROUNDS)


class DecisionNode(_Node):
    type: Literal["decision_bool"]
    condition: Condition
    # Set when one exit goes back to an earlier step; the ``limit`` exit is then taken
    # once ``max_rounds`` rounds have run. Stored only when set.
    repeat: Repeat | None = None

    @model_serializer(mode="wrap")
    def _omit_repeat(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        if self.repeat is None:
            data.pop("repeat", None)
        return data


class ApprovalNode(_Node):
    type: Literal["approval"]
    title: Template
    body: Template = []
    timeout_minutes: int | None = Field(default=None, ge=1, le=MAX_APPROVAL_MINUTES)


class TransformNode(_Node):
    type: Literal["transform"]
    template: Template = []
    fields: Annotated[dict[Identifier, Template], Field(max_length=50)] = {}


class OutputNode(_Node):
    type: Literal["output"]
    fields: Annotated[dict[Identifier, Template], Field(max_length=50)]


class FailNode(_Node):
    type: Literal["fail"]
    reason: str = Field(default="", max_length=500)


Node = Annotated[
    StartNode | AgentNode | DecisionNode | ApprovalNode | TransformNode | OutputNode | FailNode,
    Field(discriminator="type"),
]


class Edge(_Strict):
    from_: Identifier = Field(alias="from")
    to: Identifier
    when: EdgeWhen | None = None

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class WorkflowDefinition(_Strict):
    schema_version: Literal["moldy.workflow.v1"] = SCHEMA_VERSION
    inputs: Annotated[list[WorkflowInput], Field(max_length=MAX_INPUTS)] = []
    nodes: Annotated[list[Node], Field(max_length=MAX_NODES)] = []
    edges: Annotated[list[Edge], Field(max_length=MAX_EDGES)] = []

    @model_validator(mode="after")
    def _unique_names(self) -> WorkflowDefinition:
        ids = [node.id for node in self.nodes]
        if len(set(ids)) != len(ids):
            raise ValueError("node ids must be unique")
        names = [item.name for item in self.inputs]
        if len(set(names)) != len(names):
            raise ValueError("input names must be unique")
        pairs = [(edge.from_, edge.to, edge.when) for edge in self.edges]
        if len(set(pairs)) != len(pairs):
            raise ValueError("edges must be unique")
        return self

    def dump(self) -> dict[str, Any]:
        """The stored form (``from`` keys, no defaults dropped)."""
        return self.model_dump(mode="json", by_alias=True)


def empty_definition() -> dict[str, Any]:
    """A new workflow's draft: just the start node."""
    return WorkflowDefinition(nodes=[StartNode(id="start", type="start")]).dump()


__all__ = [
    "INPUT_REF",
    "MAX_NODES",
    "SCHEMA_VERSION",
    "AgentNode",
    "ApprovalNode",
    "Clause",
    "Condition",
    "DecisionNode",
    "Edge",
    "FailNode",
    "Node",
    "OutputNode",
    "Ref",
    "Repeat",
    "RefSegment",
    "StartNode",
    "TextSegment",
    "TransformNode",
    "WorkflowDefinition",
    "WorkflowInput",
    "empty_definition",
]
