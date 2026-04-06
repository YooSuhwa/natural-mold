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

interface VisualSettingsFlowProps {
  agent?: Agent
  agentId?: string
  models: Model[]
  tools: Tool[]
  skills: Skill[]
  middlewares?: MiddlewareRegistryItem[]
  triggers?: AgentTrigger[]
  mode?: 'create' | 'edit'
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
}: VisualSettingsFlowProps) {
  const router = useRouter()
  const t = useTranslations('agent.visualSettings')
  const updateAgent = useUpdateAgent(agentId ?? '')
  const createAgent = useCreateAgent()

  const [name, setName] = useState(agent?.name ?? '')
  const [description, setDescription] = useState(agent?.description ?? '')
  const [systemPrompt, setSystemPrompt] = useState(agent?.system_prompt ?? '')
  const [modelId, setModelId] = useState(agent?.model.id ?? models[0]?.id ?? '')
  const [temperature, setTemperature] = useState(agent?.model_params?.temperature ?? 0.7)
  const [topP, setTopP] = useState(agent?.model_params?.top_p ?? 1.0)
  const [maxTokens, setMaxTokens] = useState(agent?.model_params?.max_tokens ?? 4096)
  const [selectedToolIds, setSelectedToolIds] = useState<Set<string>>(
    () => new Set(agent?.tools.map((tl) => tl.id) ?? []),
  )
  const [selectedSkillIds, setSelectedSkillIds] = useState<Set<string>>(
    () => new Set(agent?.skills?.map((s) => s.id) ?? []),
  )
  const [selectedMiddlewareTypes, setSelectedMiddlewareTypes] = useState<Set<string>>(
    () => new Set(agent?.middleware_configs?.map((mc) => mc.type) ?? []),
  )

  // Sync state from agent prop (edit mode only)
  useEffect(() => {
    if (mode !== 'edit' || !agent) return
    setName(agent.name)
    setDescription(agent.description ?? '')
    setSystemPrompt(agent.system_prompt)
    setModelId(agent.model.id)
    setTemperature(agent.model_params?.temperature ?? 0.7)
    setTopP(agent.model_params?.top_p ?? 1.0)
    setMaxTokens(agent.model_params?.max_tokens ?? 4096)
    setSelectedToolIds(new Set(agent.tools.map((tl) => tl.id)))
    setSelectedSkillIds(new Set(agent.skills?.map((s) => s.id) ?? []))
    setSelectedMiddlewareTypes(new Set(agent.middleware_configs?.map((mc) => mc.type) ?? []))
  }, [agent, mode])

  // Set default model when models load in create mode
  useEffect(() => {
    if (mode === 'create' && !modelId && models.length > 0) {
      setModelId(models[0].id)
    }
  }, [mode, modelId, models])

  const toggleTool = useCallback((toolId: string) => {
    setSelectedToolIds((prev) => toggleSetItem(prev, toolId))
  }, [])

  const toggleSkill = useCallback((skillId: string) => {
    setSelectedSkillIds((prev) => toggleSetItem(prev, skillId))
  }, [])

  const toggleMiddleware = useCallback((type: string) => {
    setSelectedMiddlewareTypes((prev) => toggleSetItem(prev, type))
  }, [])

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
    [],
  )

  async function handleSave() {
    const payload = {
      name: name || (mode === 'create' ? t('defaultName') : name),
      description: description || undefined,
      system_prompt: systemPrompt,
      model_id: modelId,
      tool_ids: Array.from(selectedToolIds),
      skill_ids: Array.from(selectedSkillIds),
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
      <Toolbar
        agentId={agentId}
        agentName={name}
        onSave={handleSave}
        isSaving={isSaving}
        mode={mode}
      />
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
