"""Whether a parsed definition can run (workflow-plan 3.3): the graph and its references.

Pure: no database. Agent existence, archive state, permission and model support are
checked by ``service.agent_issues`` and appended to the same list. Every problem is an
``Issue`` the editor shows in its "확인할 것" list; publishing needs none.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Final

from app.workflows.definition import (
    INPUT_REF,
    AgentNode,
    ApprovalNode,
    Clause,
    DecisionNode,
    FailNode,
    Node,
    OutputNode,
    Ref,
    RefSegment,
    StartNode,
    TransformNode,
    WorkflowDefinition,
)

_TERMINAL: Final = (OutputNode, FailNode)
_BRANCHES: Final[dict[type, frozenset[str]]] = {
    DecisionNode: frozenset({"true", "false"}),
    ApprovalNode: frozenset({"approved", "rejected"}),
}
# What a reference may point at, per node kind (agents: their output schema).
_DECISION_FIELDS: Final = frozenset({"value", "confidence", "band", "evaluator"})
_APPROVAL_FIELDS: Final = frozenset({"decision", "comment"})


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    message: str
    node_id: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {"code": self.code, "message": self.message, "node_id": self.node_id}


def graph_issues(definition: WorkflowDefinition) -> list[Issue]:
    """Structure, branches, reachability and cycles, then references."""
    issues = _structure(definition)
    if any(issue.code in ("edge_unknown_node", "cycle") for issue in issues):
        return issues  # ancestry is meaningless on a broken or cyclic graph
    return issues + _references(definition)


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
    if any(issue.code == "edge_unknown_node" for issue in issues):
        return issues
    if _has_cycle(definition):
        issues.append(Issue("cycle", "연결이 순환합니다 (1차에는 반복이 없습니다)"))
        return issues
    if len(starts) == 1:
        reached = _reachable(definition, starts[0].id)
        issues += [
            Issue("unreachable", "시작에서 닿지 않는 노드입니다", node.id)
            for node in definition.nodes
            if node.id not in reached
        ]
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
    if expected is None:
        if whens != [None]:
            issues.append(Issue("needs_one_next", "다음 노드 연결이 하나 필요합니다", node.id))
        return issues
    if sorted(w for w in whens if w is not None) != sorted(expected) or None in whens:
        names = " / ".join(sorted(expected))
        issues.append(Issue("branch_missing", f"{names} 경로를 하나씩 연결하세요", node.id))
    return issues


def _node_fields(node: Node) -> list[Issue]:
    if isinstance(node, AgentNode) and not node.prompt:
        return [Issue("empty_prompt", "에이전트에 보낼 내용이 비어 있습니다", node.id)]
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
    if isinstance(node, AgentNode):
        templates = [node.prompt]
    elif isinstance(node, ApprovalNode):
        templates = [node.title, node.body]
    elif isinstance(node, TransformNode):
        templates = [node.template, *node.fields.values()]
    elif isinstance(node, OutputNode):
        templates = list(node.fields.values())
    elif isinstance(node, DecisionNode):
        yield from node.condition.clauses
    for template in templates:
        for segment in template:
            if isinstance(segment, RefSegment):
                yield segment.ref


def _references(definition: WorkflowDefinition) -> list[Issue]:
    nodes = {node.id: node for node in definition.nodes}
    inputs = {item.name for item in definition.inputs}
    starts = [node.id for node in definition.nodes if isinstance(node, StartNode)]
    upstream = _dominators(definition, starts[0]) if len(starts) == 1 else {}
    issues: list[Issue] = []
    for node in definition.nodes:
        if node.id not in upstream:
            continue  # unreachable nodes are reported once, as ``unreachable``
        for ref in _refs(node):
            target, path = (
                (ref.source, ref.path) if isinstance(ref, Clause) else (ref.node, ref.path)
            )
            issue = _reference_issue(target, path, node.id, nodes, inputs, upstream[node.id])
            if issue is not None:
                issues.append(issue)
    return issues


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


def _path_exists(node: Node, parts: list[str]) -> bool:
    if isinstance(node, AgentNode):
        return _schema_has(node.output_schema, parts)
    if isinstance(node, DecisionNode):
        return len(parts) == 1 and parts[0] in _DECISION_FIELDS
    if isinstance(node, ApprovalNode):
        return len(parts) == 1 and parts[0] in _APPROVAL_FIELDS
    if isinstance(node, TransformNode):
        return parts == ["text"] or (
            len(parts) == 2 and parts[0] == "fields" and parts[1] in node.fields
        )
    return False  # start and end nodes produce nothing to reference


def _schema_has(schema: dict[str, Any], parts: list[str]) -> bool:
    node: dict[str, Any] = schema
    for part in parts:
        if node.get("type") == "object" and part in node.get("properties", {}):
            node = node["properties"][part]
        elif node.get("type") == "array" and part.isdigit():
            node = node["items"]
        else:
            return False
    return True


__all__ = ["Issue", "graph_issues"]
