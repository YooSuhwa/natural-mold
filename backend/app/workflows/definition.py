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

from dataclasses import dataclass
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
# Inside a ``for_each`` node's prompt: the current item (workflow-nodes-plan 3.2).
ITEM_REF: Final = "item"
MAX_NODES: Final = 50
MAX_EDGES: Final = 200
MAX_INPUTS: Final = 20
MAX_SEGMENTS: Final = 100
MAX_TEXT: Final = 8_000
MAX_APPROVAL_MINUTES: Final = 7 * 24 * 60
MAX_FOR_EACH: Final = 20
MAX_PARALLEL: Final = 5
MAX_CASES: Final = 10

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")]
Pointer = Annotated[str, Field(pattern=r"^(/[^/]{1,64}){0,8}$")]
Label = Annotated[str, Field(max_length=80)]
Operator = Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "exists"]
# Fixed exits (decision, approval, ask, loop limit, switch default) or a switch case name.
EdgeWhen = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")]
FIXED_EXITS: Final = frozenset(
    {"true", "false", "approved", "rejected", "limit", "answered", "timeout", "default"}
)
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
    # ``file``: an upload id; agent steps that reference it get the file attached (N8).
    type: Literal["string", "number", "integer", "boolean", "file"] = "string"
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
        if value in (INPUT_REF, ITEM_REF):
            raise ValueError(f"{value!r} is reserved")
        return value


def _schema_subset(value: dict[str, Any]) -> dict[str, Any]:
    try:
        return check_output_schema(value)
    except OutputSchemaError as exc:
        raise ValueError(exc.message) from exc


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
        return _schema_subset(value)


class Repeat(_Strict):
    """How often a loop through this decision may run (rounds include the first)."""

    max_rounds: int = Field(ge=1, le=MAX_ROUNDS)


class DecisionNode(_Node):
    type: Literal["decision_bool"]
    # ``rule`` compares values (``condition``); ``llm`` asks the system model ``question``
    # (workflow-nodes-plan N3/N4). The judgment-model evaluator waits for item 22 (N9).
    evaluator: Literal["rule", "llm"] = "rule"
    condition: Condition | None = None
    question: Template | None = None
    # Set when one exit goes back to an earlier step; the ``limit`` exit is then taken
    # once ``max_rounds`` rounds have run. Stored only when set.
    repeat: Repeat | None = None

    @model_validator(mode="after")
    def _evaluator_fields(self) -> DecisionNode:
        if self.evaluator == "rule" and (self.condition is None or self.question is not None):
            raise ValueError("a rule decision has a condition and no question")
        if self.evaluator == "llm" and (self.question is None or self.condition is not None):
            raise ValueError("an llm decision has a question and no condition")
        return self

    @model_serializer(mode="wrap")
    def _omit_unset(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        for key in ("repeat", "condition", "question"):
            if data.get(key) is None:
                data.pop(key, None)
        if self.evaluator == "rule":
            data.pop("evaluator", None)
        return data


class SwitchCase(_Strict):
    name: Identifier
    value: str | int | float | bool

    @field_validator("name")
    @classmethod
    def _not_fixed(cls, value: str) -> str:
        if value in FIXED_EXITS:
            raise ValueError(f"{value!r} is a reserved exit name")
        return value


class SwitchNode(_Node):
    """Several exits by one value: the first case equal to it, else ``default``."""

    type: Literal["switch"]
    source: Identifier
    path: Pointer = ""
    cases: Annotated[list[SwitchCase], Field(max_length=MAX_CASES)] = []

    @model_validator(mode="after")
    def _unique_cases(self) -> SwitchNode:
        names = [case.name for case in self.cases]
        if len(set(names)) != len(names):
            raise ValueError("case names must be unique")
        return self


class LlmNode(_Node):
    """One call of the system model, no agent (summarize, convert, extract)."""

    type: Literal["llm"]
    prompt: Template
    output_schema: dict[str, Any]

    @field_validator("output_schema")
    @classmethod
    def _schema_in_subset(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _schema_subset(value)


class AskNode(_Node):
    """Ask the runner a question in the inbox; a written answer or the time limit."""

    type: Literal["ask"]
    question: Template
    timeout_minutes: int | None = Field(default=None, ge=1, le=MAX_APPROVAL_MINUTES)


class NotifyNode(_Node):
    """One in-app notification to the runner; the run does not stop."""

    type: Literal["notify"]
    title: Template
    body: Template = []


class ForEachNode(_Node):
    """The same agent once per item of an earlier list value (at most ``max_items``)."""

    type: Literal["for_each"]
    source: Identifier
    path: Pointer = ""
    agent_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    prompt: Template
    output_schema: dict[str, Any]
    max_items: int = Field(default=MAX_FOR_EACH, ge=1, le=MAX_FOR_EACH)

    @field_validator("output_schema")
    @classmethod
    def _schema_in_subset(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _schema_subset(value)


class ParallelBranch(_Strict):
    name: Identifier
    agent_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    prompt: Template
    output_schema: dict[str, Any]

    @field_validator("output_schema")
    @classmethod
    def _schema_in_subset(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _schema_subset(value)


class ParallelNode(_Node):
    """Several agents at once on the same values; their outputs merged by branch name."""

    type: Literal["parallel"]
    branches: Annotated[list[ParallelBranch], Field(max_length=MAX_PARALLEL)] = []

    @model_validator(mode="after")
    def _unique_branches(self) -> ParallelNode:
        names = [branch.name for branch in self.branches]
        if len(set(names)) != len(names):
            raise ValueError("branch names must be unique")
        return self


ToolName = Literal["web_search", "web_scraper", "current_datetime"]
# The arguments each read-only tool takes (N7).
TOOL_ARGS: Final[dict[str, frozenset[str]]] = {
    "web_search": frozenset({"query"}),
    "web_scraper": frozenset({"url"}),
    "current_datetime": frozenset(),
}


class ToolNode(_Node):
    """A read-only built-in tool called directly (outside effects stay in agent steps)."""

    type: Literal["tool"]
    tool: ToolName
    args: Annotated[dict[Identifier, Template], Field(max_length=10)] = {}


class Approver(_Strict):
    """Who decides an approval (starts plan 3.4): the runner, the workflow's owner, or a
    named person who may run the workflow."""

    kind: Literal["runner", "owner", "user"]
    user_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")

    @model_validator(mode="after")
    def _user_needs_id(self) -> Approver:
        if (self.kind == "user") != (self.user_id is not None):
            raise ValueError("a named approver has a user_id, and only it")
        return self


class ApprovalNode(_Node):
    type: Literal["approval"]
    title: Template
    body: Template = []
    timeout_minutes: int | None = Field(default=None, ge=1, le=MAX_APPROVAL_MINUTES)
    # The runner when unset (stored only when set).
    approver: Approver | None = None

    @model_serializer(mode="wrap")
    def _omit_approver(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        if self.approver is None:
            data.pop("approver", None)
        return data


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
    StartNode
    | AgentNode
    | DecisionNode
    | ApprovalNode
    | TransformNode
    | OutputNode
    | FailNode
    | SwitchNode
    | LlmNode
    | AskNode
    | NotifyNode
    | ForEachNode
    | ParallelNode
    | ToolNode,
    Field(discriminator="type"),
]


@dataclass(frozen=True, slots=True)
class AgentUse:
    """An agent a node runs. ``key`` is the pin key: the node id, or ``node.branch``."""

    key: str
    node_id: str
    agent_id: str


def agent_uses(nodes: list[Any]) -> list[AgentUse]:
    uses: list[AgentUse] = []
    for node in nodes:
        if isinstance(node, AgentNode | ForEachNode):
            uses.append(AgentUse(node.id, node.id, node.agent_id))
        elif isinstance(node, ParallelNode):
            uses += [
                AgentUse(f"{node.id}.{branch.name}", node.id, branch.agent_id)
                for branch in node.branches
            ]
    return uses


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
    "FIXED_EXITS",
    "INPUT_REF",
    "ITEM_REF",
    "MAX_NODES",
    "SCHEMA_VERSION",
    "TOOL_ARGS",
    "AgentNode",
    "AgentUse",
    "ApprovalNode",
    "Approver",
    "AskNode",
    "ForEachNode",
    "LlmNode",
    "NotifyNode",
    "ParallelBranch",
    "ParallelNode",
    "SwitchCase",
    "SwitchNode",
    "ToolNode",
    "agent_uses",
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
