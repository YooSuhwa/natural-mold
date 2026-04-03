'use client'

import { useState, useEffect, useMemo, useCallback } from 'react'
import { ReactFlow, useNodesState, useEdgesState, Panel } from '@xyflow/react'
import type { Node, Edge } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { toast } from 'sonner'
import { useTranslations } from 'next-intl'
import { useUpdateAgent } from '@/lib/hooks/use-agents'
import type { Agent, Model, Tool, Skill, AgentTrigger } from '@/lib/types'
import { Toolbar } from './toolbar'
import { AgentNode } from './nodes/agent-node'
import { ChannelsNode } from './nodes/channels-node'
import { SubagentsNode } from './nodes/subagents-node'
import { ToolboxNode } from './nodes/toolbox-node'
import { SkillsNode } from './nodes/skills-node'
import { ScheduleNode } from './nodes/schedule-node'

interface VisualSettingsFlowProps {
  agent: Agent
  agentId: string
  models: Model[]
  tools: Tool[]
  skills: Skill[]
  triggers: AgentTrigger[]
}

export function VisualSettingsFlow({
  agent,
  agentId,
  models,
  tools,
  skills,
  triggers,
}: VisualSettingsFlowProps) {
  const t = useTranslations('agent.visualSettings')
  const updateAgent = useUpdateAgent(agentId)

  const [name, setName] = useState(agent.name)
  const [description, setDescription] = useState(agent.description ?? '')
  const [systemPrompt, setSystemPrompt] = useState(agent.system_prompt)
  const [modelId, setModelId] = useState(agent.model.id)
  const [temperature, setTemperature] = useState(agent.model_params?.temperature ?? 0.7)
  const [topP, setTopP] = useState(agent.model_params?.top_p ?? 1.0)
  const [maxTokens, setMaxTokens] = useState(agent.model_params?.max_tokens ?? 4096)
  const [selectedToolIds, setSelectedToolIds] = useState<Set<string>>(
    () => new Set(agent.tools.map((tl) => tl.id)),
  )
  const [selectedSkillIds, setSelectedSkillIds] = useState<Set<string>>(
    () => new Set(agent.skills?.map((s) => s.id) ?? []),
  )

  useEffect(() => {
    setName(agent.name)
    setDescription(agent.description ?? '')
    setSystemPrompt(agent.system_prompt)
    setModelId(agent.model.id)
    setTemperature(agent.model_params?.temperature ?? 0.7)
    setTopP(agent.model_params?.top_p ?? 1.0)
    setMaxTokens(agent.model_params?.max_tokens ?? 4096)
    setSelectedToolIds(new Set(agent.tools.map((tl) => tl.id)))
    setSelectedSkillIds(new Set(agent.skills?.map((s) => s.id) ?? []))
  }, [agent])

  const toggleTool = useCallback((toolId: string) => {
    setSelectedToolIds((prev) => {
      const next = new Set(prev)
      if (next.has(toolId)) next.delete(toolId)
      else next.add(toolId)
      return next
    })
  }, [])

  const toggleSkill = useCallback((skillId: string) => {
    setSelectedSkillIds((prev) => {
      const next = new Set(prev)
      if (next.has(skillId)) next.delete(skillId)
      else next.add(skillId)
      return next
    })
  }, [])

  const currentModelName = useMemo(() => {
    return models.find((m) => m.id === modelId)?.display_name ?? agent.model.display_name
  }, [models, modelId, agent.model.display_name])

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
    try {
      await updateAgent.mutateAsync({
        name,
        description: description || undefined,
        system_prompt: systemPrompt,
        model_id: modelId,
        tool_ids: Array.from(selectedToolIds),
        skill_ids: Array.from(selectedSkillIds),
        model_params: { temperature, top_p: topP, max_tokens: maxTokens },
      })
      toast.success(t('toast.saved'))
    } catch {
      toast.error(t('toast.saveFailed'))
    }
  }

  const nodeTypes = useMemo(
    () => ({
      agent: AgentNode,
      channels: ChannelsNode,
      subagents: SubagentsNode,
      toolbox: ToolboxNode,
      skills: SkillsNode,
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
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )

  const hasSchedules = triggers.length > 0
  const hasTools = selectedToolIds.size > 0
  const hasSkills = selectedSkillIds.size > 0

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
    ],
    [hasSchedules, hasTools, hasSkills],
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
    selectedToolIds,
    selectedSkillIds,
    toggleTool,
    toggleSkill,
    setNodes,
  ])

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <Toolbar
        agentId={agentId}
        agentName={name}
        onSave={handleSave}
        isSaving={updateAgent.isPending}
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
