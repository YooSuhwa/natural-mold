'use client'

import { SectionInstructions } from './section-instructions'
import { SectionSubAgents } from './section-sub-agents'
import { SectionModel } from './section-model'
import { ToolsMiddlewaresGrid } from './tools-middlewares-grid'

interface FormModeProps {
  systemPrompt: string
  onSystemPromptChange: (v: string) => void

  selectedSubAgentIds: Set<string>
  onToggleSubAgent: (id: string) => void
  /** 자기 자신 필터링용. 매뉴얼 페이지는 빈 문자열 전달. */
  currentAgentId: string

  modelId: string
  onModelIdChange: (v: string) => void
  temperature: number
  onTemperatureChange: (v: number) => void
  topP: number
  onTopPChange: (v: number) => void
  maxTokens: number
  onMaxTokensChange: (v: number) => void
  onResetModelParams: () => void
  fallbackIds?: string[]
  onFallbackIdsChange?: (ids: string[]) => void

  selectedToolIds: Set<string>
  onToggleTool: (id: string) => void
  selectedSkillIds: Set<string>
  onToggleSkill: (id: string) => void
  selectedMiddlewareTypes: Set<string>
  onToggleMiddleware: (type: string) => void
}

export function FormMode({
  systemPrompt,
  onSystemPromptChange,
  selectedSubAgentIds,
  onToggleSubAgent,
  currentAgentId,
  modelId,
  onModelIdChange,
  temperature,
  onTemperatureChange,
  topP,
  onTopPChange,
  maxTokens,
  onMaxTokensChange,
  onResetModelParams,
  fallbackIds,
  onFallbackIdsChange,
  selectedToolIds,
  onToggleTool,
  selectedSkillIds,
  onToggleSkill,
  selectedMiddlewareTypes,
  onToggleMiddleware,
}: FormModeProps) {
  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-hidden p-4">
      <SectionInstructions
        systemPrompt={systemPrompt}
        onSystemPromptChange={onSystemPromptChange}
      />
      <SectionSubAgents
        selectedSubAgentIds={selectedSubAgentIds}
        onToggleSubAgent={onToggleSubAgent}
        currentAgentId={currentAgentId}
      />
      <SectionModel
        modelId={modelId}
        onModelIdChange={onModelIdChange}
        temperature={temperature}
        onTemperatureChange={onTemperatureChange}
        topP={topP}
        onTopPChange={onTopPChange}
        maxTokens={maxTokens}
        onMaxTokensChange={onMaxTokensChange}
        onReset={onResetModelParams}
        fallbackIds={fallbackIds}
        onFallbackIdsChange={onFallbackIdsChange}
      />
      <ToolsMiddlewaresGrid
        selectedToolIds={selectedToolIds}
        onToggleTool={onToggleTool}
        selectedSkillIds={selectedSkillIds}
        onToggleSkill={onToggleSkill}
        selectedMiddlewareTypes={selectedMiddlewareTypes}
        onToggleMiddleware={onToggleMiddleware}
      />
    </div>
  )
}
