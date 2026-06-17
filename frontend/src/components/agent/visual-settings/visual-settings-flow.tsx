'use client'

import { useState, useEffect, useMemo, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { ReactFlow, useNodesState, useEdgesState, Panel } from '@xyflow/react'
import type { Node, Edge } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { toast } from 'sonner'
import { useTranslations } from 'next-intl'
import { useUpdateAgent, useCreateAgent } from '@/lib/hooks/use-agents'
import { useCreateTrigger, useDeleteTrigger, useUpdateTrigger } from '@/lib/hooks/use-triggers'
import { toggleSetItem } from '@/lib/utils'
import type {
  Agent,
  Model,
  Tool,
  Skill,
  AgentTrigger,
  AgentIdentityMode,
  MiddlewareRegistryItem,
  TriggerCreateRequest,
  TriggerUpdateRequest,
} from '@/lib/types'
import { Toolbar } from './toolbar'
import { AgentNode } from './nodes/agent-node'
import { ChannelsNode } from './nodes/channels-node'
import { SubagentsNode } from './nodes/subagents-node'
import { ToolboxNode } from './nodes/toolbox-node'
import { SkillsNode } from './nodes/skills-node'
import { MiddlewaresNode } from './nodes/middlewares-node'
import { ScheduleNode } from './nodes/schedule-node'

interface ControlledVisualState {
  name: string
  description: string
  systemPrompt: string
  modelId: string
  identityMode: AgentIdentityMode
  temperature: number
  topP: number
  maxTokens: number
  selectedToolIds: Set<string>
  selectedMcpToolIds: Set<string>
  selectedSkillIds: Set<string>
  selectedSubAgentIds: Set<string>
  selectedMiddlewareTypes: Set<string>
}

interface ControlledVisualHandlers {
  onNameChange: (v: string) => void
  onDescriptionChange: (v: string) => void
  onSystemPromptChange: (v: string) => void
  onModelIdChange: (v: string) => void
  onIdentityModeChange: (v: AgentIdentityMode) => void
  onTemperatureChange: (v: number) => void
  onTopPChange: (v: number) => void
  onMaxTokensChange: (v: number) => void
  onToggleTool: (id: string) => void
  onToggleMcpTool: (id: string) => void
  onToggleSkill: (id: string) => void
  onToggleSubAgent: (id: string) => void
  onToggleMiddleware: (type: string) => void
}

interface ControlledVisualEditor {
  state: ControlledVisualState
  handlers: ControlledVisualHandlers
}

export interface VisualSettingsFlowProps {
  agent?: Agent
  agentId?: string
  models: Model[]
  tools: Tool[]
  skills: Skill[]
  middlewares?: MiddlewareRegistryItem[]
  triggers?: AgentTrigger[]
  mode?: 'create' | 'edit'
  /**
   * 워크벤치(`/agents/[id]/settings`) 안에서 inline 렌더할 때 true.
   * - 내부 Toolbar(별도 Save 버튼) 숨김 → workbench 헤더 단일 Save 사용
   * - controlledState/controlledHandlers가 함께 제공되면 모든 편집 state는 페이지 owns
   */
  embedded?: boolean
  controlledState?: ControlledVisualState
  controlledHandlers?: ControlledVisualHandlers
}

const FLOW_EDGE = {
  schedule: 'var(--flow-edge-schedule)',
  toolbox: 'var(--flow-edge-toolbox)',
  subagents: 'var(--flow-edge-subagents)',
  skills: 'var(--flow-edge-skills)',
  middlewares: 'var(--flow-edge-middlewares)',
} as const

const EMPTY_MIDDLEWARES: MiddlewareRegistryItem[] = []
const EMPTY_TRIGGERS: AgentTrigger[] = []

function edgeStyle(stroke: string, active: boolean, opacity = 0.3): Edge['style'] {
  return active
    ? { stroke, strokeWidth: 2 }
    : { stroke, strokeWidth: 2, strokeDasharray: '5 5', opacity }
}

export function VisualSettingsFlow({
  agent,
  agentId,
  models,
  tools,
  skills,
  middlewares = EMPTY_MIDDLEWARES,
  triggers = EMPTY_TRIGGERS,
  mode = 'edit',
  embedded = false,
  controlledState,
  controlledHandlers,
}: VisualSettingsFlowProps) {
  const router = useRouter()
  const t = useTranslations('agent.visualSettings')
  const updateAgent = useUpdateAgent(agentId ?? '')
  const createAgent = useCreateAgent()
  const { mutate: createTriggerMutate, isPending: isCreatingTrigger } = useCreateTrigger(
    agentId ?? '',
  )
  const { mutate: updateTriggerMutate, isPending: isUpdatingTrigger } = useUpdateTrigger(
    agentId ?? '',
  )
  const { mutate: deleteTriggerMutate, isPending: isDeletingTrigger } = useDeleteTrigger(
    agentId ?? '',
  )
  const handleCreateTrigger = useCallback(
    (data: TriggerCreateRequest) => {
      if (!agentId) return
      createTriggerMutate(data)
    },
    [agentId, createTriggerMutate],
  )
  const handleUpdateTrigger = useCallback(
    (triggerId: string, data: TriggerUpdateRequest) => {
      if (!agentId) return
      updateTriggerMutate({ triggerId, data })
    },
    [agentId, updateTriggerMutate],
  )
  const handleDeleteTrigger = useCallback(
    (triggerId: string) => {
      if (!agentId) return
      deleteTriggerMutate(triggerId)
    },
    [agentId, deleteTriggerMutate],
  )
  const isTriggerPending = isCreatingTrigger || isUpdatingTrigger || isDeletingTrigger

  // Internal state (uncontrolled fallback). 사용되지 않을 때도 hooks 규칙상 항상 호출.
  const [internalName, setInternalName] = useState(agent?.name ?? '')
  const [internalDescription, setInternalDescription] = useState(agent?.description ?? '')
  const [internalSystemPrompt, setInternalSystemPrompt] = useState(agent?.system_prompt ?? '')
  const [internalModelId, setInternalModelId] = useState(agent?.model?.id ?? models[0]?.id ?? '')
  const [internalIdentityMode, setInternalIdentityMode] = useState<AgentIdentityMode>(
    agent?.identity_mode ?? 'per_user',
  )
  const [internalTemperature, setInternalTemperature] = useState(
    agent?.model_params?.temperature ?? 0.7,
  )
  const [internalTopP, setInternalTopP] = useState(agent?.model_params?.top_p ?? 1.0)
  const [internalMaxTokens, setInternalMaxTokens] = useState(
    agent?.model_params?.max_tokens ?? 4096,
  )
  const [internalSelectedToolIds, setInternalSelectedToolIds] = useState<Set<string>>(
    () => new Set(agent?.tools.map((tl) => tl.id) ?? []),
  )
  const [internalSelectedMcpToolIds, setInternalSelectedMcpToolIds] = useState<Set<string>>(
    () => new Set(agent?.mcp_tools?.map((mt) => mt.id) ?? []),
  )
  const [internalSelectedSkillIds, setInternalSelectedSkillIds] = useState<Set<string>>(
    () => new Set(agent?.skills?.map((s) => s.id) ?? []),
  )
  const [internalSelectedSubAgentIds, setInternalSelectedSubAgentIds] = useState<Set<string>>(
    () => new Set(agent?.sub_agents?.map((sa) => sa.id) ?? []),
  )
  const [internalSelectedMiddlewareTypes, setInternalSelectedMiddlewareTypes] = useState<
    Set<string>
  >(() => new Set(agent?.middleware_configs?.map((mc) => mc.type) ?? []))

  // controlledState 제공 시 모든 편집 state는 페이지(workbench) owns. 그렇지 않으면 internal state.
  const controlledEditor = useMemo<ControlledVisualEditor | null>(() => {
    if (!embedded || !controlledState || !controlledHandlers) return null
    return { state: controlledState, handlers: controlledHandlers }
  }, [embedded, controlledState, controlledHandlers])
  const isControlled = controlledEditor !== null
  const name = controlledEditor ? controlledEditor.state.name : internalName
  const description = controlledEditor ? controlledEditor.state.description : internalDescription
  const systemPrompt = controlledEditor ? controlledEditor.state.systemPrompt : internalSystemPrompt
  const modelId = controlledEditor ? controlledEditor.state.modelId : internalModelId
  const identityMode = controlledEditor ? controlledEditor.state.identityMode : internalIdentityMode
  const temperature = controlledEditor ? controlledEditor.state.temperature : internalTemperature
  const topP = controlledEditor ? controlledEditor.state.topP : internalTopP
  const maxTokens = controlledEditor ? controlledEditor.state.maxTokens : internalMaxTokens
  const selectedToolIds = controlledEditor
    ? controlledEditor.state.selectedToolIds
    : internalSelectedToolIds
  const selectedMcpToolIds = controlledEditor
    ? controlledEditor.state.selectedMcpToolIds
    : internalSelectedMcpToolIds
  const selectedSkillIds = controlledEditor
    ? controlledEditor.state.selectedSkillIds
    : internalSelectedSkillIds
  const selectedSubAgentIds = controlledEditor
    ? controlledEditor.state.selectedSubAgentIds
    : internalSelectedSubAgentIds
  const selectedMiddlewareTypes = controlledEditor
    ? controlledEditor.state.selectedMiddlewareTypes
    : internalSelectedMiddlewareTypes

  const setName = controlledEditor ? controlledEditor.handlers.onNameChange : setInternalName
  const setDescription = controlledEditor
    ? controlledEditor.handlers.onDescriptionChange
    : setInternalDescription
  const setSystemPrompt = controlledEditor
    ? controlledEditor.handlers.onSystemPromptChange
    : setInternalSystemPrompt
  const setModelId = controlledEditor
    ? controlledEditor.handlers.onModelIdChange
    : setInternalModelId
  const setTemperature = controlledEditor
    ? controlledEditor.handlers.onTemperatureChange
    : setInternalTemperature
  const setTopP = controlledEditor ? controlledEditor.handlers.onTopPChange : setInternalTopP
  const setMaxTokens = controlledEditor
    ? controlledEditor.handlers.onMaxTokensChange
    : setInternalMaxTokens

  // Sync state from agent prop (edit mode only, uncontrolled only)
  useEffect(() => {
    if (isControlled) return
    if (mode !== 'edit' || !agent) return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- uncontrolled edit form mirrors loaded agent props.
    setInternalName(agent.name)
    setInternalDescription(agent.description ?? '')
    setInternalSystemPrompt(agent.system_prompt)
    setInternalModelId(agent.model?.id ?? '')
    setInternalIdentityMode(agent.identity_mode)
    setInternalTemperature(agent.model_params?.temperature ?? 0.7)
    setInternalTopP(agent.model_params?.top_p ?? 1.0)
    setInternalMaxTokens(agent.model_params?.max_tokens ?? 4096)
    setInternalSelectedToolIds(new Set(agent.tools.map((tl) => tl.id)))
    setInternalSelectedMcpToolIds(new Set(agent.mcp_tools?.map((mt) => mt.id) ?? []))
    setInternalSelectedSkillIds(new Set(agent.skills?.map((s) => s.id) ?? []))
    setInternalSelectedSubAgentIds(new Set(agent.sub_agents?.map((sa) => sa.id) ?? []))
    setInternalSelectedMiddlewareTypes(
      new Set(agent.middleware_configs?.map((mc) => mc.type) ?? []),
    )
  }, [agent, mode, isControlled])

  // Set default model when models load in create mode (uncontrolled only)
  useEffect(() => {
    if (isControlled) return
    if (mode === 'create' && !internalModelId && models.length > 0) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- initial default model is selected when async models arrive.
      setInternalModelId(models[0].id)
    }
  }, [mode, internalModelId, models, isControlled])

  const toggleTool = useCallback(
    (toolId: string) => {
      if (controlledEditor) {
        controlledEditor.handlers.onToggleTool(toolId)
      } else {
        setInternalSelectedToolIds((prev) => toggleSetItem(prev, toolId))
      }
    },
    [controlledEditor],
  )

  const toggleMcpTool = useCallback(
    (id: string) => {
      if (controlledEditor) {
        controlledEditor.handlers.onToggleMcpTool(id)
      } else {
        setInternalSelectedMcpToolIds((prev) => toggleSetItem(prev, id))
      }
    },
    [controlledEditor],
  )

  const toggleSkill = useCallback(
    (skillId: string) => {
      if (controlledEditor) {
        controlledEditor.handlers.onToggleSkill(skillId)
      } else {
        setInternalSelectedSkillIds((prev) => toggleSetItem(prev, skillId))
      }
    },
    [controlledEditor],
  )

  const toggleSubAgent = useCallback(
    (subAgentId: string) => {
      if (controlledEditor) {
        controlledEditor.handlers.onToggleSubAgent(subAgentId)
      } else {
        setInternalSelectedSubAgentIds((prev) => toggleSetItem(prev, subAgentId))
      }
    },
    [controlledEditor],
  )

  const toggleMiddleware = useCallback(
    (type: string) => {
      if (controlledEditor) {
        controlledEditor.handlers.onToggleMiddleware(type)
      } else {
        setInternalSelectedMiddlewareTypes((prev) => toggleSetItem(prev, type))
      }
    },
    [controlledEditor],
  )

  const currentModelName = useMemo(() => {
    const found = models.find((m) => m.id === modelId)?.display_name
    if (found) return found
    return agent?.model?.display_name ?? ''
  }, [models, modelId, agent?.model?.display_name])

  const handleAgentNodeUpdate = useCallback(
    (data: {
      name: string
      description: string
      modelId: string
      systemPrompt: string
      temperature: number
      topP: number
      maxTokens: number
    }) => {
      setName(data.name)
      setDescription(data.description)
      setModelId(data.modelId)
      setSystemPrompt(data.systemPrompt)
      setTemperature(data.temperature)
      setTopP(data.topP)
      setMaxTokens(data.maxTokens)
    },
    // setX are dynamic (controlled vs internal) — recompute when they swap
    [setName, setDescription, setModelId, setSystemPrompt, setTemperature, setTopP, setMaxTokens],
  )

  async function handleSave() {
    const payload = {
      name: name || (mode === 'create' ? t('defaultName') : name),
      description: description || undefined,
      system_prompt: systemPrompt,
      model_id: modelId,
      identity_mode: identityMode,
      tool_ids: Array.from(selectedToolIds),
      mcp_tool_ids: Array.from(selectedMcpToolIds),
      skill_ids: Array.from(selectedSkillIds),
      sub_agent_ids: Array.from(selectedSubAgentIds),
      middleware_configs: Array.from(selectedMiddlewareTypes).map((type) => ({
        type,
        params: {},
      })),
      model_params: { temperature, top_p: topP, max_tokens: maxTokens },
    }

    try {
      if (mode === 'create') {
        const created = await createAgent.mutateAsync(payload)
        toast.success(t('toast.saved'))
        router.push(`/agents/${created.id}`)
      } else {
        await updateAgent.mutateAsync(payload)
        toast.success(t('toast.saved'))
      }
    } catch {
      toast.error(t('toast.saveFailed'))
    }
  }

  const isSaving = mode === 'create' ? createAgent.isPending : updateAgent.isPending

  const nodeTypes = useMemo(
    () => ({
      agent: AgentNode,
      channels: ChannelsNode,
      subagents: SubagentsNode,
      toolbox: ToolboxNode,
      skills: SkillsNode,
      middlewares: MiddlewaresNode,
      schedule: ScheduleNode,
    }),
    [],
  )

  const initialNodes: Node[] = useMemo(
    () => [
      {
        id: 'schedule',
        type: 'schedule',
        position: { x: -510, y: 90 },
        data: {},
      },
      {
        id: 'channels',
        type: 'channels',
        position: { x: -510, y: 270 },
        data: {},
      },
      {
        id: 'agent',
        type: 'agent',
        position: { x: -165, y: 138 },
        data: {
          name,
          description,
          modelId,
          modelName: currentModelName,
          systemPrompt,
          temperature,
          topP,
          maxTokens,
          models,
          onUpdate: handleAgentNodeUpdate,
        },
      },
      {
        id: 'toolbox',
        type: 'toolbox',
        position: { x: 210, y: 0 },
        data: {
          allTools: tools,
          selectedToolIds,
          onToggleTool: toggleTool,
          selectedMcpToolIds,
          onToggleMcpTool: toggleMcpTool,
          allSkills: skills,
          selectedSkillIds,
          onToggleSkill: toggleSkill,
        },
      },
      {
        id: 'subagents',
        type: 'subagents',
        position: { x: 210, y: 175 },
        data: {
          selectedSubAgentIds,
          onToggleSubAgent: toggleSubAgent,
          currentAgentId: agentId ?? '',
        },
      },
      {
        id: 'skills',
        type: 'skills',
        position: { x: 210, y: 365 },
        data: { allSkills: skills, selectedSkillIds, onToggleSkill: toggleSkill },
      },
      {
        id: 'middlewares',
        type: 'middlewares',
        position: { x: 210, y: 540 },
        data: {
          allMiddlewares: middlewares,
          selectedTypes: selectedMiddlewareTypes,
          onToggleMiddleware: toggleMiddleware,
        },
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )

  const hasSchedules = triggers.length > 0
  const hasTools = selectedToolIds.size > 0
  const hasSkills = selectedSkillIds.size > 0
  const hasSubAgents = selectedSubAgentIds.size > 0
  const hasMiddlewares = selectedMiddlewareTypes.size > 0

  const computedEdges: Edge[] = useMemo(
    () => [
      {
        id: 'schedules-agent',
        source: 'schedule',
        target: 'agent',
        animated: hasSchedules,
        style: edgeStyle(FLOW_EDGE.schedule, hasSchedules, 0.25),
      },
      {
        id: 'channels-agent',
        source: 'channels',
        target: 'agent',
        style: edgeStyle(FLOW_EDGE.schedule, false, 0.25),
      },
      {
        id: 'agent-toolbox',
        source: 'agent',
        target: 'toolbox',
        animated: hasTools,
        style: edgeStyle(FLOW_EDGE.toolbox, hasTools),
      },
      {
        id: 'agent-subagents',
        source: 'agent',
        target: 'subagents',
        animated: hasSubAgents,
        style: edgeStyle(FLOW_EDGE.subagents, hasSubAgents),
      },
      {
        id: 'agent-skills',
        source: 'agent',
        target: 'skills',
        animated: hasSkills,
        style: edgeStyle(FLOW_EDGE.skills, hasSkills),
      },
      {
        id: 'agent-middlewares',
        source: 'agent',
        target: 'middlewares',
        animated: hasMiddlewares,
        style: edgeStyle(FLOW_EDGE.middlewares, hasMiddlewares),
      },
    ],
    [hasSchedules, hasTools, hasSkills, hasSubAgents, hasMiddlewares],
  )

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(computedEdges)

  useEffect(() => {
    setEdges(computedEdges)
  }, [computedEdges, setEdges])

  useEffect(() => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === 'agent') {
          return {
            ...node,
            data: {
              ...node.data,
              name,
              description,
              modelId,
              modelName: currentModelName,
              systemPrompt,
              temperature,
              topP,
              maxTokens,
              models,
              onUpdate: handleAgentNodeUpdate,
            },
          }
        }
        if (node.id === 'schedule') {
          return {
            ...node,
            data: {
              triggers,
              agentId: agentId ?? '',
              onCreateTrigger: handleCreateTrigger,
              onUpdateTrigger: handleUpdateTrigger,
              onDeleteTrigger: handleDeleteTrigger,
              isPending: isTriggerPending,
            },
          }
        }
        if (node.id === 'toolbox') {
          return {
            ...node,
            data: {
              allTools: tools,
              selectedToolIds,
              onToggleTool: toggleTool,
              selectedMcpToolIds,
              onToggleMcpTool: toggleMcpTool,
              allSkills: skills,
              selectedSkillIds,
              onToggleSkill: toggleSkill,
            },
          }
        }
        if (node.id === 'subagents') {
          return {
            ...node,
            data: {
              selectedSubAgentIds,
              onToggleSubAgent: toggleSubAgent,
              currentAgentId: agentId ?? '',
            },
          }
        }
        if (node.id === 'skills') {
          return {
            ...node,
            data: { allSkills: skills, selectedSkillIds, onToggleSkill: toggleSkill },
          }
        }
        if (node.id === 'middlewares') {
          return {
            ...node,
            data: {
              allMiddlewares: middlewares,
              selectedTypes: selectedMiddlewareTypes,
              onToggleMiddleware: toggleMiddleware,
            },
          }
        }
        return node
      }),
    )
  }, [
    name,
    description,
    modelId,
    currentModelName,
    systemPrompt,
    temperature,
    topP,
    maxTokens,
    models,
    handleAgentNodeUpdate,
    tools,
    skills,
    middlewares,
    selectedToolIds,
    selectedMcpToolIds,
    selectedSkillIds,
    selectedSubAgentIds,
    selectedMiddlewareTypes,
    toggleTool,
    toggleMcpTool,
    toggleSkill,
    toggleSubAgent,
    toggleMiddleware,
    agentId,
    triggers,
    handleCreateTrigger,
    handleUpdateTrigger,
    handleDeleteTrigger,
    isTriggerPending,
    setNodes,
  ])

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {!embedded && (
        <Toolbar
          agentId={agentId}
          agentName={name}
          onSave={handleSave}
          isSaving={isSaving}
          mode={mode}
        />
      )}
      <div className="flex-1">
        <ReactFlow
          className="moldy-visual-flow"
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.3 }}
          proOptions={{ hideAttribution: true }}
          nodesDraggable
          nodesConnectable={false}
          elementsSelectable={false}
        >
          <Panel position="bottom-center" className="text-xs text-muted-foreground">
            {t('canvas.hint')}
          </Panel>
        </ReactFlow>
      </div>
    </div>
  )
}
