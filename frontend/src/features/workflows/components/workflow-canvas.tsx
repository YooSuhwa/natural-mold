'use client'

import { Background, Controls, MarkerType, ReactFlow } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useMemo } from 'react'

import { useFlowColorMode } from '@/components/shared/use-flow-color-mode'
import type { EdgeWhen, WorkflowDefinition, WorkflowEdge } from '@/lib/types/workflow'

import { freeExits } from '../lib/editor-commands'
import { layout, NODE_HEIGHT, NODE_WIDTH } from '../lib/layout'
import { edgeKey, type DiffStatus } from '../lib/version-diff'
import { WorkflowEdgeLine, type WorkflowFlowEdge } from './workflow-edge'
import { handleId, WorkflowNodeCard, type WorkflowFlowNode } from './workflow-node'

const NODE_TYPES = { workflow: WorkflowNodeCard }
const EDGE_TYPES = { workflow: WorkflowEdgeLine }

export interface CanvasProps {
  definition: WorkflowDefinition
  titleOf: (nodeId: string) => { title: string; subtitle: string; detail: string }
  showDetail: boolean
  issueCounts: Map<string, number>
  selectedId: string | null
  onSelect: (nodeId: string | null) => void
  /** Without these the canvas is read only (the version comparison): no "+" buttons. */
  onInsert?: (edge: WorkflowEdge) => void
  onAddAfter?: (nodeId: string, when: EdgeWhen | null) => void
  /** Colours each node and connection by how it differs from the compared version. */
  diff?: { nodes: Map<string, DiffStatus>; edges: Map<string, DiffStatus> }
}

/** Auto-laid-out canvas (Q7): nodes are not dragged, the layout is the reading order. */
export function WorkflowCanvas(props: CanvasProps) {
  const colorMode = useFlowColorMode()
  const {
    definition,
    titleOf,
    showDetail,
    issueCounts,
    selectedId,
    onSelect,
    onInsert,
    onAddAfter,
    diff,
  } = props
  const nodes = useMemo<WorkflowFlowNode[]>(() => {
    const placed = layout(definition)
    return definition.nodes.map((node) => {
      const at = placed.get(node.id)
      return {
        id: node.id,
        type: 'workflow',
        position: { x: at?.x ?? 0, y: at?.y ?? 0 },
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
        draggable: false,
        data: {
          type: node.type,
          ...titleOf(node.id),
          showDetail,
          issueCount: issueCounts.get(node.id) ?? 0,
          selected: node.id === selectedId,
          freeExits: onAddAfter ? freeExits(definition, node.id) : [],
          onAddAfter,
          diff: diff?.nodes.get(node.id),
        },
      }
    })
  }, [definition, titleOf, showDetail, issueCounts, selectedId, onAddAfter, diff])
  const edges = useMemo<WorkflowFlowEdge[]>(
    () =>
      definition.edges.map((edge) => {
        const status = diff?.edges.get(edgeKey(edge))
        return {
          id: edgeKey(edge),
          source: edge.from,
          sourceHandle: handleId(edge.when ?? null),
          target: edge.to,
          type: 'workflow',
          className: status && status !== 'same' ? `moldy-flow-diff-${status}` : undefined,
          markerEnd: { type: MarkerType.ArrowClosed },
          data: onInsert ? { edge, onInsert } : undefined,
        }
      }),
    [definition, onInsert, diff],
  )
  return (
    <ReactFlow
      className="moldy-visual-flow"
      colorMode={colorMode}
      nodes={nodes}
      edges={edges}
      nodeTypes={NODE_TYPES}
      edgeTypes={EDGE_TYPES}
      nodesDraggable={false}
      nodesConnectable={false}
      fitView
      fitViewOptions={{ padding: 0.25 }}
      // Below the 0.5 default so a long workflow still fits (the version comparison
      // draws the base's removed steps too).
      minZoom={0.2}
      proOptions={{ hideAttribution: true }}
      onNodeClick={(_, node) => onSelect(node.id)}
      onPaneClick={() => onSelect(null)}
    >
      <Background />
      <Controls showInteractive={false} />
    </ReactFlow>
  )
}
