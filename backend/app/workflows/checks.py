"""Whether a parsed definition can run (workflow-plan 3.3): the graph and its references.

Loops (workflow-loop-plan 3.2): the only cycle allowed is a **back-edge** from a decision's
``true``/``false`` exit to a step that runs before it on every path. Its **body** (the steps
from that target to the decision) is checked as a unit: a ``repeat`` limit and a ``limit``
exit are required, bodies may not overlap, hold no approval/output/fail step, and leave
only through the decision. Everything else (reachability, branches, references) is checked
on the **forward** graph, the definition without its back-edges.

Pure: no database. Agent existence, archive state, permission and model support are
checked by ``service.agent_issues`` and appended to the same list. Every problem is an
``Issue`` the editor shows in its "확인할 것" list; publishing needs none.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Final

from app.workflows.definition import (
    INPUT_REF,
    ITEM_REF,
    MAX_PARALLEL,
    TOOL_ARGS,
    AgentNode,
    ApprovalNode,
    AskNode,
    Clause,
    DecisionNode,
    Edge,
    FailNode,
    ForEachNode,
    LlmNode,
    Node,
    NotifyNode,
    OutputNode,
    ParallelNode,
    Ref,
    RefSegment,
    StartNode,
    SwitchNode,
    ToolNode,
    TransformNode,
    WorkflowDefinition,
)

_TERMINAL: Final = (OutputNode, FailNode)
_BRANCHES: Final[dict[type, frozenset[str]]] = {
    DecisionNode: frozenset({"true", "false"}),
    ApprovalNode: frozenset({"approved", "rejected"}),
    AskNode: frozenset({"answered", "timeout"}),
}
# What a reference may point at, per node kind (agents: their output schema).
_DECISION_FIELDS: Final = frozenset({"value", "confidence", "band", "evaluator", "reason"})
_APPROVAL_FIELDS: Final = frozenset({"decision", "comment"})
_SWITCH_FIELDS: Final = frozenset({"value", "case"})


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    message: str
    node_id: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {"code": self.code, "message": self.message, "node_id": self.node_id}


@dataclass(frozen=True, slots=True)
class Loop:
    """A back-edge ``decision --when--> target`` and the steps it runs again."""

    decision: str
    when: str | None
    target: str
    body: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class LoopShape:
    loops: list[Loop]
    forward: WorkflowDefinition  # the definition without its back-edges
    issues: list[Issue]


def graph_issues(definition: WorkflowDefinition) -> list[Issue]:
    """Structure, branches, reachability and cycles, then loops, then references."""
    issues = _structure(definition)
    if any(issue.code == "edge_unknown_node" for issue in issues):
        return issues  # a broken graph has no meaningful shape
    shape = loop_shape(definition)
    issues += shape.issues
    if any(issue.code == "cycle" for issue in issues):
        return issues  # ancestry is meaningless on a cyclic graph
    issues += _reachability(shape.forward)
    return issues + _references(shape.forward, shape.loops)


def _structure(definition: WorkflowDefinition) -> list[Issue]:
    nodes = {node.id: node for node in definition.nodes}
    issues: list[Issue] = []
    starts = [node for node in definition.nodes if isinstance(node, StartNode)]
    if not starts:
        issues.append(Issue("no_start", "시작 노드가 없습니다"))
    elif len(starts) > 1:
        issues += [Issue("many_starts", "시작 노드는 하나만 둘 수 있습니다", n.id) for n in starts]
    if not any(isinstance(node, _TERMINAL) for node in definition.nodes):
        issues.append(Issue("no_end", "최종 출력이나 실패 종료 노드가 하나 이상 필요합니다"))

    outgoing: dict[str, list[str | None]] = defaultdict(list)
    incoming: dict[str, int] = defaultdict(int)
    for edge in definition.edges:
        missing = [end for end in (edge.from_, edge.to) if end not in nodes]
        if missing:
            issues += [Issue("edge_unknown_node", f"없는 노드 {m!r}를 잇습니다") for m in missing]
            continue
        outgoing[edge.from_].append(edge.when)
        incoming[edge.to] += 1

    for node in definition.nodes:
        issues += _node_edges(node, outgoing[node.id], incoming[node.id])
        issues += _node_fields(node)
    return issues


def _reachability(forward: WorkflowDefinition) -> list[Issue]:
    starts = [node for node in forward.nodes if isinstance(node, StartNode)]
    if len(starts) != 1:
        return []
    reached = _reachable(forward, starts[0].id)
    return [
        Issue("unreachable", "시작에서 닿지 않는 노드입니다", node.id)
        for node in forward.nodes
        if node.id not in reached
    ]


def loop_shape(definition: WorkflowDefinition) -> LoopShape:
    """Find the back-edges (depth-first from start), check them, and the forward graph."""
    nodes = {node.id: node for node in definition.nodes}
    starts = [node.id for node in definition.nodes if isinstance(node, StartNode)]
    back = _back_edges(definition, starts[0]) if len(starts) == 1 else []
    forward = definition.model_copy(
        update={"edges": [edge for edge in definition.edges if edge not in back]}
    )
    if _has_cycle(forward):
        return LoopShape([], forward, [Issue("cycle", "연결이 순환합니다")])
    issues: list[Issue] = []
    dominators = _dominators(forward, starts[0]) if starts else {}
    loops: list[Loop] = []
    for edge in back:
        source = nodes[edge.from_]
        if edge.to not in dominators.get(edge.from_, set()):
            issues.append(Issue("cycle", "연결이 순환합니다"))
            return LoopShape([], forward, issues)
        if not isinstance(source, DecisionNode) or edge.when not in ("true", "false"):
            message = "되돌아가는 연결은 판단 노드의 예/아니오 갈래에서만 둘 수 있습니다"
            issues.append(Issue("loop_from_not_decision", message, edge.from_))
            continue
        loops.append(Loop(edge.from_, edge.when, edge.to, _body(forward, edge.to, edge.from_)))
    issues += _loop_rules(definition, forward, loops)
    return LoopShape(loops, forward, issues)


def _back_edges(definition: WorkflowDefinition, start: str) -> list[Edge]:
    """Edges to a node still on the depth-first path (the classic retreating edges)."""
    out: dict[str, list[Edge]] = defaultdict(list)
    for edge in definition.edges:
        out[edge.from_].append(edge)
    back: list[Edge] = []
    on_path: set[str] = set()
    done: set[str] = set()

    def visit(node: str) -> None:
        on_path.add(node)
        for edge in out[node]:
            if edge.to in on_path:
                back.append(edge)
            elif edge.to not in done:
                visit(edge.to)
        on_path.discard(node)
        done.add(node)

    visit(start)
    return back


def _body(forward: WorkflowDefinition, target: str, decision: str) -> frozenset[str]:
    preceding: dict[str, list[str]] = defaultdict(list)
    for edge in forward.edges:
        preceding[edge.to].append(edge.from_)
    reaches_decision, queue = {decision}, deque([decision])
    while queue:
        for parent in preceding[queue.popleft()]:
            if parent not in reaches_decision:
                reaches_decision.add(parent)
                queue.append(parent)
    return frozenset(reaches_decision & _reachable(forward, target))


def _loop_rules(
    definition: WorkflowDefinition, forward: WorkflowDefinition, loops: list[Loop]
) -> list[Issue]:
    nodes = {node.id: node for node in definition.nodes}
    issues: list[Issue] = []
    looping = {loop.decision for loop in loops}
    for node in definition.nodes:
        if not isinstance(node, DecisionNode):
            continue
        limit = [
            edge for edge in definition.edges if edge.from_ == node.id and edge.when == "limit"
        ]
        if node.id not in looping:
            if node.repeat is not None or limit:
                message = "되돌아가는 연결이 없는데 반복 설정이나 한도 초과 갈래가 있습니다"
                issues.append(Issue("loop_settings_without_loop", message, node.id))
            continue
        if node.repeat is None:
            issues.append(Issue("loop_limit_missing", "반복 최대 횟수를 정하세요", node.id))
        if not limit:
            message = "반복 한도를 넘었을 때 갈 단계(한도 초과 갈래)를 연결하세요"
            issues.append(Issue("loop_limit_exit_missing", message, node.id))
    seen: set[str] = set()
    for loop in sorted(loops, key=lambda item: item.decision):
        if loop.decision in seen or any(
            other is not loop and other.body & loop.body for other in loops
        ):
            message = "반복끼리 겹치거나 반복 안에 반복을 둘 수 없습니다"
            issues.append(Issue("loop_overlap", message, loop.decision))
        seen.add(loop.decision)
        for member in sorted(loop.body):
            if isinstance(nodes[member], ApprovalNode | AskNode | OutputNode | FailNode):
                message = (
                    "반복 안에는 승인, 사람에게 묻기, 최종 출력, 실패 종료 노드를 둘 수 없습니다"
                )
                issues.append(Issue("loop_body_kind", message, member))
        for edge in forward.edges:
            leaves = edge.from_ in loop.body and edge.to not in loop.body
            if leaves and edge.from_ != loop.decision:
                message = "반복 밖으로는 반복을 정하는 판단 노드에서만 나갈 수 있습니다"
                issues.append(Issue("loop_exit", message, edge.from_))
            if edge.from_ == loop.decision and edge.when == "limit" and edge.to in loop.body:
                message = "한도 초과 갈래는 반복 밖의 단계로 이어야 합니다"
                issues.append(Issue("loop_exit", message, edge.from_))
    return issues


def _node_edges(node: Node, whens: list[str | None], incoming: int) -> list[Issue]:
    # A node nothing leads to is reported once, as ``unreachable``.
    issues: list[Issue] = []
    if isinstance(node, StartNode) and incoming:
        issues.append(Issue("start_has_incoming", "시작 노드로 들어오는 연결이 있습니다", node.id))
    if isinstance(node, _TERMINAL):
        if whens:
            issues.append(Issue("end_has_outgoing", "끝 노드에서 나가는 연결이 있습니다", node.id))
        return issues
    expected = _BRANCHES.get(type(node))
    if isinstance(node, SwitchNode):
        expected = frozenset({*(case.name for case in node.cases), "default"})
    if isinstance(node, DecisionNode):
        whens = [when for when in whens if when != "limit"]  # a loop's exit: _loop_rules
    if expected is None:
        if whens != [None]:
            issues.append(Issue("needs_one_next", "다음 노드 연결이 하나 필요합니다", node.id))
        return issues
    if sorted(w for w in whens if w is not None) != sorted(expected) or None in whens:
        names = " / ".join(sorted(expected))
        issues.append(Issue("branch_missing", f"{names} 경로를 하나씩 연결하세요", node.id))
    return issues


def _node_fields(node: Node) -> list[Issue]:
    if isinstance(node, AgentNode | ForEachNode) and not node.prompt:
        return [Issue("empty_prompt", "에이전트에 보낼 내용이 비어 있습니다", node.id)]
    if isinstance(node, LlmNode) and not node.prompt:
        return [Issue("empty_prompt", "모델에 보낼 내용이 비어 있습니다", node.id)]
    if isinstance(node, DecisionNode) and node.evaluator == "llm" and not node.question:
        return [Issue("empty_question", "모델에 물을 질문이 비어 있습니다", node.id)]
    if isinstance(node, SwitchNode) and not node.cases:
        return [Issue("switch_no_cases", "경우를 하나 이상 정하세요", node.id)]
    if isinstance(node, AskNode):
        issues = [] if node.question else [Issue("empty_question", "질문이 비어 있습니다", node.id)]
        if node.timeout_minutes is None:
            issues.append(Issue("ask_no_timeout", "답을 기다릴 시간 제한을 정하세요", node.id))
        return issues
    if isinstance(node, NotifyNode) and not node.title:
        return [Issue("empty_title", "알림 제목이 비어 있습니다", node.id)]
    if isinstance(node, ParallelNode):
        if not 2 <= len(node.branches) <= MAX_PARALLEL:
            message = f"병렬 에이전트는 2~{MAX_PARALLEL}개여야 합니다"
            return [Issue("parallel_branches", message, node.id)]
        if any(not branch.prompt for branch in node.branches):
            return [Issue("empty_prompt", "에이전트에 보낼 내용이 비어 있습니다", node.id)]
    if isinstance(node, ToolNode):
        missing = sorted(TOOL_ARGS[node.tool] - {k for k, v in node.args.items() if v})
        extra = sorted(set(node.args) - TOOL_ARGS[node.tool])
        if missing or extra:
            names = ", ".join(missing + extra)
            return [Issue("tool_args", f"도구 인자를 확인하세요: {names}", node.id)]
    if isinstance(node, ApprovalNode):
        issues = [] if node.title else [Issue("empty_title", "승인 제목이 비어 있습니다", node.id)]
        if node.timeout_minutes is None:
            issues.append(Issue("approval_no_timeout", "승인 시간 제한을 정하세요", node.id))
        return issues
    if isinstance(node, OutputNode) and not node.fields:
        return [Issue("empty_output", "최종 출력 필드가 없습니다", node.id)]
    return []


def _successors(definition: WorkflowDefinition) -> dict[str, list[str]]:
    following: dict[str, list[str]] = defaultdict(list)
    for edge in definition.edges:
        following[edge.from_].append(edge.to)
    return following


def _has_cycle(definition: WorkflowDefinition) -> bool:
    return len(_topological(definition)) != len(definition.nodes)


def _reachable(definition: WorkflowDefinition, start: str) -> set[str]:
    following = _successors(definition)
    seen, queue = {start}, deque([start])
    while queue:
        for target in following[queue.popleft()]:
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return seen


def _dominators(definition: WorkflowDefinition, start: str) -> dict[str, set[str]]:
    """For each reachable node, the nodes that run before it on every path from start.

    A reference may only name one of these: a node on just some paths (the approved
    branch, say) may not have run when this one does.
    """
    preceding: dict[str, set[str]] = defaultdict(set)
    for edge in definition.edges:
        preceding[edge.to].add(edge.from_)
    order = _topological(definition)
    dominators: dict[str, set[str]] = {}
    for current in order:
        parents = [dominators[p] | {p} for p in preceding[current] if p in dominators]
        if current == start:
            dominators[current] = set()
        elif parents:
            dominators[current] = set.intersection(*parents)
    return dominators


def _topological(definition: WorkflowDefinition) -> list[str]:
    following = _successors(definition)
    degree = {node.id: 0 for node in definition.nodes}
    for targets in following.values():
        for target in targets:
            degree[target] += 1
    ready = deque(node for node, count in degree.items() if count == 0)
    order: list[str] = []
    while ready:
        current = ready.popleft()
        order.append(current)
        for target in following[current]:
            degree[target] -= 1
            if degree[target] == 0:
                ready.append(target)
    return order


def _refs(node: Node) -> Iterator[Ref | Clause]:
    templates: list[list[Any]] = []
    if isinstance(node, AgentNode | LlmNode | ForEachNode):
        templates = [node.prompt]
    elif isinstance(node, ApprovalNode | NotifyNode):
        templates = [node.title, node.body]
    elif isinstance(node, AskNode):
        templates = [node.question]
    elif isinstance(node, TransformNode):
        templates = [node.template, *node.fields.values()]
    elif isinstance(node, OutputNode):
        templates = list(node.fields.values())
    elif isinstance(node, ParallelNode):
        templates = [branch.prompt for branch in node.branches]
    elif isinstance(node, ToolNode):
        templates = list(node.args.values())
    elif isinstance(node, DecisionNode):
        if node.condition is not None:
            yield from node.condition.clauses
        templates = [node.question or []]
    if isinstance(node, SwitchNode | ForEachNode):
        yield Ref(node=node.source, path=node.path)
    for template in templates:
        for segment in template:
            if isinstance(segment, RefSegment):
                yield segment.ref


def _references(definition: WorkflowDefinition, loops: list[Loop]) -> list[Issue]:
    """References on the forward graph; ``previous`` ones only inside the owner's loop."""
    nodes = {node.id: node for node in definition.nodes}
    inputs = {item.name for item in definition.inputs}
    starts = [node.id for node in definition.nodes if isinstance(node, StartNode)]
    upstream = _dominators(definition, starts[0]) if len(starts) == 1 else {}
    issues: list[Issue] = []
    for node in definition.nodes:
        if node.id not in upstream:
            continue  # unreachable nodes are reported once, as ``unreachable``
        body = next((loop.body for loop in loops if node.id in loop.body), frozenset())
        for ref in _refs(node):
            target, path = (
                (ref.source, ref.path) if isinstance(ref, Clause) else (ref.node, ref.path)
            )
            if target == ITEM_REF:
                if not isinstance(node, ForEachNode):
                    message = "항목(item) 값은 항목마다 노드 안에서만 쓸 수 있습니다"
                    issues.append(Issue("ref_item_scope", message, node.id))
                continue
            if isinstance(ref, Ref) and ref.previous:
                if target not in body or target in upstream[node.id]:
                    message = (
                        "앞 회차 값은 같은 반복 안에서 이 단계보다 뒤에 오는 단계만 쓸 수 있습니다"
                    )
                    issues.append(Issue("ref_previous_scope", message, node.id))
                elif not _path_exists(nodes[target], path.split("/")[1:]):
                    issues.append(
                        Issue("ref_bad_path", f"{target!r}에 {path!r} 값이 없습니다", node.id)
                    )
                continue
            issue = _reference_issue(target, path, node.id, nodes, inputs, upstream[node.id])
            if issue is not None:
                issues.append(issue)
        if isinstance(node, ForEachNode) and (
            node.source == INPUT_REF or node.source in upstream[node.id]
        ):
            source = nodes.get(node.source)
            if source is None or not _is_list(source, node.path.split("/")[1:]):
                message = "항목마다 노드의 대상은 목록(배열) 값이어야 합니다"
                issues.append(Issue("for_each_not_list", message, node.id))
    return issues


def _is_list(node: Node, parts: list[str]) -> bool:
    schema = _output_schema(node)
    if schema is None:
        return False
    current: dict[str, Any] | None = schema
    for part in parts:
        current = _schema_child(current, part)
        if current is None:
            return False
    return current.get("type") == "array"


def _reference_issue(
    target: str,
    path: str,
    owner: str,
    nodes: dict[str, Node],
    inputs: set[str],
    upstream: set[str],
) -> Issue | None:
    parts = path.split("/")[1:]
    if target == INPUT_REF:
        if len(parts) != 1 or parts[0] not in inputs:
            return Issue("ref_unknown_input", f"없는 입력 {path!r}을 참조합니다", owner)
        return None
    if target not in nodes:
        return Issue("ref_unknown_node", f"없는 노드 {target!r}를 참조합니다", owner)
    if target not in upstream:
        message = f"{target!r}는 이 노드로 오는 모든 경로에서 앞에 있지 않습니다"
        return Issue("ref_not_upstream", message, owner)
    if not _path_exists(nodes[target], parts):
        return Issue("ref_bad_path", f"{target!r}에 {path!r} 값이 없습니다", owner)
    return None


def _output_schema(node: Node) -> dict[str, Any] | None:
    """The JSON shape of a node's output, for nodes whose output has a declared shape."""
    if isinstance(node, AgentNode | LlmNode):
        return node.output_schema
    if isinstance(node, ForEachNode):
        return {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": node.output_schema},
                "count": {"type": "integer"},
            },
        }
    if isinstance(node, ParallelNode):
        return {
            "type": "object",
            "properties": {branch.name: branch.output_schema for branch in node.branches},
        }
    return None


def _path_exists(node: Node, parts: list[str]) -> bool:
    schema = _output_schema(node)
    if schema is not None:
        return _schema_has(schema, parts)
    fields: frozenset[str] | None = None
    if isinstance(node, DecisionNode):
        fields = _DECISION_FIELDS
    elif isinstance(node, ApprovalNode):
        fields = _APPROVAL_FIELDS
    elif isinstance(node, SwitchNode):
        fields = _SWITCH_FIELDS
    elif isinstance(node, AskNode):
        fields = frozenset({"answer"})
    elif isinstance(node, NotifyNode):
        fields = frozenset({"sent"})
    elif isinstance(node, ToolNode):
        fields = frozenset({"result"})
    if fields is not None:
        return len(parts) == 1 and parts[0] in fields
    if isinstance(node, TransformNode):
        return parts == ["text"] or (
            len(parts) == 2 and parts[0] == "fields" and parts[1] in node.fields
        )
    return False  # start and end nodes produce nothing to reference


def _schema_child(node: dict[str, Any] | None, part: str) -> dict[str, Any] | None:
    if node is None:
        return None
    if node.get("type") == "object" and part in node.get("properties", {}):
        child: dict[str, Any] = node["properties"][part]
        return child
    if node.get("type") == "array" and part.isdigit():
        item: dict[str, Any] = node["items"]
        return item
    return None


def _schema_has(schema: dict[str, Any], parts: list[str]) -> bool:
    node: dict[str, Any] | None = schema
    for part in parts:
        node = _schema_child(node, part)
        if node is None:
            return False
    return True


__all__ = ["Issue", "Loop", "LoopShape", "graph_issues", "loop_shape"]
