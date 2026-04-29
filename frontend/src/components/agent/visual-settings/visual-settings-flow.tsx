'use client'

import { useState, useEffect, useMemo, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { ReactFlow, useNodesState, useEdgesState, Panel } from '@xyflow/react'
import type { Node, Edge } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { toast } from 'sonner'
import { useTranslations } from 'next-intl'
import { useUpdateAgent, useCreateAgent } from '@/lib/hooks/use-agents'
import { toggleSetItem } from '@/lib/utils'
import type { Agent, Model, Tool, Skill, AgentTrigger, MiddlewareRegistryItem } from '@/lib/types'
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
  temperature: number
  topP: number
  maxTokens: number
  selectedToolIds: Set<string>
  selectedSkillIds: Set<string>
  selectedSubAgentIds: Set<string>
  selectedMiddlewareTypes: Set<string>
}

interface ControlledVisualHandlers {
  onNameChange: (v: string) => void
  onDescriptionChange: (v: string) => void
  onSystemPromptChange: (v: string) => void
  onModelIdChange: (v: string) => void
  onTemperatureChange: (v: number) => void
  onTopPChange: (v: number) => void
  onMaxTokensChange: (v: number) => void
  onToggleTool: (id: string) => void
  onToggleSkill: (id: string) => void
  onToggleSubAgent: (id: string) => void
  onToggleMiddleware: (type: string) => void
}

interface VisualSettingsFlowProps {
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

export function VisualSettingsFlow({
  agent,
  agentId,
  models,
  tools,
  skills,
  middlewares = [],
  triggers = [],
  mode = 'edit',
  embedded = false,
  controlledState,
  controlledHandlers,
}: VisualSettingsFlowProps) {
  const router = useRouter()
  const t = useTranslations('agent.visualSettings')
  const updateAgent = useUpdateAgent(agentId ?? '')
  const createAgent = useCreateAgent()

  // Internal state (uncontrolled fallback). 사용되지 않을 때도 hooks 규칙상 항상 호출.
  const [internalName, setInternalName] = useState(agent?.name ?? '')
  const [internalDescription, setInternalDescription] = useState(agent?.description ?? '')
  const [internalSystemPrompt, setInternalSystemPrompt] = useState(agent?.system_prompt ?? '')
  const [internalModelId, setInternalModelId] = useState(agent?.model.id ?? models[0]?.id ?? '')
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
  const isControlled = embedded && !!controlledState && !!controlledHandlers
  const name = isControlled ? controlledState!.name : internalName
  const description = isControlled ? controlledState!.description : internalDescription
  const systemPrompt = isControlled ? controlledState!.systemPrompt : internalSystemPrompt
  const modelId = isControlled ? controlledState!.modelId : internalModelId
  const temperature = isControlled ? controlledState!.temperature : internalTemperature
  const topP = isControlled ? controlledState!.topP : internalTopP
  const maxTokens = isControlled ? controlledState!.maxTokens : internalMaxTokens
  const selectedToolIds = isControlled ? controlledState!.selectedToolIds : internalSelectedToolIds
  const selectedSkillIds = isControlled
    ? controlledState!.selectedSkillIds
    : internalSelectedSkillIds
  const selectedSubAgentIds = isControlled
    ? controlledState!.selectedSubAgentIds
    : internalSelectedSubAgentIds
  const selectedMiddlewareTypes = isControlled
    ? controlledState!.selectedMiddlewareTypes
    : internalSelectedMiddlewareTypes

  const setName = isControlled ? controlledHandlers!.onNameChange : setInternalName
  const setDescription = isControlled
    ? controlledHandlers!.onDescriptionChange
    : setInternalDescription
  const setSystemPrompt = isControlled
    ? controlledHandlers!.onSystemPromptChange
    : setInternalSystemPrompt
  const setModelId = isControlled ? controlledHandlers!.onModelIdChange : setInternalModelId
  const setTemperature = isControlled
    ? controlledHandlers!.onTemperatureChange
    : setInternalTemperature
  const setTopP = isControlled ? controlledHandlers!.onTopPChange : setInternalTopP
  const setMaxTokens = isControlled
    ? controlledHandlers!.onMaxTokensChange
    : setInternalMaxTokens

  // Sync state from agent prop (edit mode only, uncontrolled only)
  useEffect(() => {
    if (isControlled) return
    if (mode !== 'edit' || !agent) return
    setInternalName(agent.name)
    setInternalDescription(agent.description ?? '')
    setInternalSystemPrompt(agent.system_prompt)
    setInternalModelId(agent.model.id)
    setInternalTemperature(agent.model_params?.temperature ?? 0.7)
    setInternalTopP(agent.model_params?.top_p ?? 1.0)
    setInternalMaxTokens(agent.model_params?.max_tokens ?? 4096)
    setInternalSelectedToolIds(new Set(agent.tools.map((tl) => tl.id)))
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
      setInternalModelId(models[0].id)
    }
  }, [mode, internalModelId, models, isControlled])

  const toggleTool = useCallback(
    (toolId: string) => {
      if (isControlled) {
        controlledHandlers!.onToggleTool(toolId)
      } else {
        setInternalSelectedToolIds((prev) => toggleSetItem(prev, toolId))
      }
    },
    [isControlled, controlledHandlers],
  )

  const toggleSkill = useCallback(
    (skillId: string) => {
      if (isControlled) {
        controlledHandlers!.onToggleSkill(skillId)
      } else {
        setInternalSelectedSkillIds((prev) => toggleSetItem(prev, skillId))
      }
    },
    [isControlled, controlledHandlers],
  )

  // sub-agent state pass-through (visual 모드의 노드 시각화는 후속 PR — state 일관성만 유지).
  // 사용되진 않더라도 controlled 분기에서 toggle 호출 가능성을 유지하기 위해 필요.
  const toggleSubAgent = useCallback(
    (subAgentId: string) => {
      if (isControlled) {
        controlledHandlers!.onToggleSubAgent(subAgentId)
      } else {
        setInternalSelectedSubAgentIds((prev) => toggleSetItem(prev, subAgentId))
      }
    },
    [isControlled, controlledHandlers],
  )
  // 미사용 변수 경고 회피 (후속 PR에서 노드에 연결 예정)
  void selectedSubAgentIds
  void toggleSubAgent

  const toggleMiddleware = useCallback(
    (type: string) => {
      if (isControlled) {
        controlledHandlers!.onToggleMiddleware(type)
      } else {
        setInternalSelectedMiddlewareTypes((prev) => toggleSetItem(prev, type))
      }
    },
    [isControlled, controlledHandlers],
  )

  const currentModelName = useMemo(() => {
    const found = models.find((m) => m.id === modelId)?.display_name
    if (found) return found
    return agent?.model.display_name ?? ''
  }, [models, modelId, agent?.model.display_name])

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
      tool_ids: Array.from(selectedToolIds),
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
        data: { allTools: tools, selectedToolIds, onToggleTool: toggleTool },
      },
      {
        id: 'subagents',
        type: 'subagents',
        position: { x: 210, y: 175 },
        data: {},
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
  const hasMiddlewares = selectedMiddlewareTypes.size > 0

  const computedEdges: Edge[] = useMemo(
    () => [
      {
        id: 'schedules-agent',
        source: 'schedule',
        target: 'agent',
        animated: hasSchedules,
        style: hasSchedules
          ? { stroke: '#f59e0b', strokeWidth: 2 }
          : { stroke: '#f59e0b', strokeWidth: 2, strokeDasharray: '5 5', opacity: 0.25 },
      },
      {
        id: 'channels-agent',
        source: 'channels',
        target: 'agent',
        style: { stroke: '#f59e0b', strokeWidth: 2, strokeDasharray: '5 5', opacity: 0.25 },
      },
      {
        id: 'agent-toolbox',
        source: 'agent',
        target: 'toolbox',
        animated: hasTools,
        style: hasTools
          ? { stroke: '#6366f1', strokeWidth: 2 }
          : { stroke: '#6366f1', strokeWidth: 2, strokeDasharray: '5 5', opacity: 0.3 },
      },
      {
        id: 'agent-subagents',
        source: 'agent',
        target: 'subagents',
        style: { stroke: '#8b5cf6', strokeWidth: 2, strokeDasharray: '5 5', opacity: 0.3 },
      },
      {
        id: 'agent-skills',
        source: 'agent',
        target: 'skills',
        animated: hasSkills,
        style: hasSkills
          ? { stroke: '#10b981', strokeWidth: 2 }
          : { stroke: '#10b981', strokeWidth: 2, strokeDasharray: '5 5', opacity: 0.3 },
      },
      {
        id: 'agent-middlewares',
        source: 'agent',
        target: 'middlewares',
        animated: hasMiddlewares,
        style: hasMiddlewares
          ? { stroke: '#f59e0b', strokeWidth: 2 }
          : { stroke: '#f59e0b', strokeWidth: 2, strokeDasharray: '5 5', opacity: 0.3 },
      },
    ],
    [hasSchedules, hasTools, hasSkills, hasMiddlewares],
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
        if (node.id === 'toolbox') {
          return { ...node, data: { allTools: tools, selectedToolIds, onToggleTool: toggleTool } }
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
    selectedSkillIds,
    selectedMiddlewareTypes,
    toggleTool,
    toggleSkill,
    toggleMiddleware,
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
