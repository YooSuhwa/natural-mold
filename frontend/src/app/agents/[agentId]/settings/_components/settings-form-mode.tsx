'use client'

import { useAgentAccess } from '@/components/agent/agent-access-context'

import type { AgentSettingsDraft } from '../_lib/agent-settings-draft'
import type { AgentSettingsDraftActions } from '../_hooks/use-agent-settings-draft'
import { FormMode } from './form-mode/form-mode'

interface SettingsFormModeProps {
  agentId: string
  draft: AgentSettingsDraft
  actions: AgentSettingsDraftActions
}

/** The settings form bound to the draft; read-only without `can_edit` (plan 13.9 row 2). */
export function SettingsFormMode({ agentId, draft, actions }: SettingsFormModeProps) {
  const { canEdit } = useAgentAccess()
  return (
    <fieldset
      disabled={!canEdit}
      data-testid="agent-settings-form"
      className="flex min-h-0 flex-1 flex-col"
    >
      <FormMode
        systemPrompt={draft.systemPrompt}
        onSystemPromptChange={actions.setSystemPrompt}
        selectedSubAgentIds={draft.selectedSubAgentIds}
        onToggleSubAgent={actions.toggleSubAgent}
        currentAgentId={agentId}
        modelId={draft.modelId}
        onModelIdChange={actions.setModelId}
        temperature={draft.temperature}
        onTemperatureChange={actions.setTemperature}
        topP={draft.topP}
        onTopPChange={actions.setTopP}
        maxTokens={draft.maxTokens}
        onMaxTokensChange={actions.setMaxTokens}
        onResetModelParams={actions.resetModelParams}
        fallbackIds={draft.fallbackIds}
        onFallbackIdsChange={actions.setFallbackIds}
        selectedToolIds={draft.selectedToolIds}
        onToggleTool={actions.toggleTool}
        selectedMcpToolIds={draft.selectedMcpToolIds}
        onToggleMcpTool={actions.toggleMcpTool}
        selectedSkillIds={draft.selectedSkillIds}
        onToggleSkill={actions.toggleSkill}
        selectedMiddlewareTypes={draft.selectedMiddlewareTypes}
        onToggleMiddleware={actions.toggleMiddleware}
        requiredLinkIds={draft.requiredLinkIds}
        onToggleRequired={actions.toggleRequired}
      />
    </fieldset>
  )
}
