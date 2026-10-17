'use client'

import { useMemo } from 'react'

import type { AgentSettingsDraft } from '../_lib/agent-settings-draft'
import type { AgentSettingsDraftActions } from './use-agent-settings-draft'

/** The embedded visual canvas edits the same settings draft as the form. */
export function useVisualControlled(draft: AgentSettingsDraft, actions: AgentSettingsDraftActions) {
  const state = useMemo(
    () => ({
      name: draft.name,
      description: draft.description,
      systemPrompt: draft.systemPrompt,
      modelId: draft.modelId,
      identityMode: draft.identityMode,
      temperature: draft.temperature,
      topP: draft.topP,
      maxTokens: draft.maxTokens,
      selectedToolIds: draft.selectedToolIds,
      selectedMcpToolIds: draft.selectedMcpToolIds,
      selectedSkillIds: draft.selectedSkillIds,
      selectedSubAgentIds: draft.selectedSubAgentIds,
      selectedMiddlewareTypes: draft.selectedMiddlewareTypes,
    }),
    [draft],
  )
  const handlers = useMemo(
    () => ({
      onNameChange: actions.setName,
      onDescriptionChange: actions.setDescription,
      onSystemPromptChange: actions.setSystemPrompt,
      onModelIdChange: actions.setModelId,
      onIdentityModeChange: actions.setIdentityMode,
      onTemperatureChange: actions.setTemperature,
      onTopPChange: actions.setTopP,
      onMaxTokensChange: actions.setMaxTokens,
      onToggleTool: actions.toggleTool,
      onToggleMcpTool: actions.toggleMcpTool,
      onToggleSkill: actions.toggleSkill,
      onToggleSubAgent: actions.toggleSubAgent,
      onToggleMiddleware: actions.toggleMiddleware,
    }),
    [actions],
  )
  return { state, handlers }
}
