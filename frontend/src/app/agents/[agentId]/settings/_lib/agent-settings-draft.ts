import type { Agent, AgentIdentityMode, AgentUpdateRequest, RuntimePolicyV1 } from '@/lib/types'
import { arraysEqual, setsEqual } from '@/lib/utils'

export const DEFAULT_TEMPERATURE = 0.7
export const DEFAULT_TOP_P = 1.0
export const DEFAULT_MAX_TOKENS = 4096

export type AgentSettingsDraft = {
  name: string
  description: string
  systemPrompt: string
  modelId: string
  identityMode: AgentIdentityMode
  fallbackIds: string[]
  selectedToolIds: Set<string>
  selectedMcpToolIds: Set<string>
  selectedSkillIds: Set<string>
  selectedSubAgentIds: Set<string>
  temperature: number
  topP: number
  maxTokens: number
  selectedMiddlewareTypes: Set<string>
  openerQuestions: string[]
  runtimePolicy: RuntimePolicyV1 | null
}

function cloneRuntimePolicy(policy: RuntimePolicyV1 | null | undefined): RuntimePolicyV1 | null {
  if (policy === null || policy === undefined) return null
  return {
    version: policy.version,
    filesystem: { mode: policy.filesystem.mode },
    todo: { enabled: policy.todo.enabled },
    summarization:
      policy.summarization.mode === 'auto'
        ? { mode: 'auto' }
        : { mode: 'preset', preset: policy.summarization.preset },
  }
}

function runtimePoliciesEqual(
  left: RuntimePolicyV1 | null,
  right: RuntimePolicyV1 | null,
): boolean {
  if (left === null || right === null) return left === right
  if (
    left.version !== right.version ||
    left.filesystem.mode !== right.filesystem.mode ||
    left.todo.enabled !== right.todo.enabled ||
    left.summarization.mode !== right.summarization.mode
  ) {
    return false
  }
  if (left.summarization.mode === 'auto') return true
  return (
    right.summarization.mode === 'preset' &&
    left.summarization.preset === right.summarization.preset
  )
}

export function createEmptyAgentSettingsDraft(): AgentSettingsDraft {
  return {
    name: '',
    description: '',
    systemPrompt: '',
    modelId: '',
    identityMode: 'per_user',
    fallbackIds: [],
    selectedToolIds: new Set(),
    selectedMcpToolIds: new Set(),
    selectedSkillIds: new Set(),
    selectedSubAgentIds: new Set(),
    temperature: DEFAULT_TEMPERATURE,
    topP: DEFAULT_TOP_P,
    maxTokens: DEFAULT_MAX_TOKENS,
    selectedMiddlewareTypes: new Set(),
    openerQuestions: [],
    runtimePolicy: null,
  }
}

export function buildAgentSettingsDraftFromAgent(agent: Agent): AgentSettingsDraft {
  return {
    name: agent.name,
    description: agent.description ?? '',
    systemPrompt: agent.system_prompt,
    modelId: agent.model?.id ?? '',
    identityMode: agent.identity_mode,
    fallbackIds: [...(agent.model_fallback_ids ?? [])],
    selectedToolIds: new Set(agent.tools.map((tool) => tool.id)),
    selectedMcpToolIds: new Set(agent.mcp_tools?.map((tool) => tool.id) ?? []),
    selectedSkillIds: new Set(agent.skills?.map((skill) => skill.id) ?? []),
    selectedSubAgentIds: new Set(agent.sub_agents?.map((subAgent) => subAgent.id) ?? []),
    temperature: agent.model_params?.temperature ?? DEFAULT_TEMPERATURE,
    topP: agent.model_params?.top_p ?? DEFAULT_TOP_P,
    maxTokens: agent.model_params?.max_tokens ?? DEFAULT_MAX_TOKENS,
    selectedMiddlewareTypes: new Set(
      agent.middleware_configs?.map((middleware) => middleware.type) ?? [],
    ),
    openerQuestions: [...(agent.opener_questions ?? [])],
    runtimePolicy: cloneRuntimePolicy(agent.runtime_policy),
  }
}

export function isAgentSettingsDraftDirty(
  draft: AgentSettingsDraft,
  baseline: AgentSettingsDraft | null,
): boolean {
  if (!baseline) return false
  return (
    draft.name !== baseline.name ||
    draft.description !== baseline.description ||
    draft.systemPrompt !== baseline.systemPrompt ||
    draft.modelId !== baseline.modelId ||
    draft.identityMode !== baseline.identityMode ||
    draft.temperature !== baseline.temperature ||
    draft.topP !== baseline.topP ||
    draft.maxTokens !== baseline.maxTokens ||
    !setsEqual(draft.selectedToolIds, baseline.selectedToolIds) ||
    !setsEqual(draft.selectedMcpToolIds, baseline.selectedMcpToolIds) ||
    !setsEqual(draft.selectedSkillIds, baseline.selectedSkillIds) ||
    !setsEqual(draft.selectedSubAgentIds, baseline.selectedSubAgentIds) ||
    !setsEqual(draft.selectedMiddlewareTypes, baseline.selectedMiddlewareTypes) ||
    !arraysEqual(draft.openerQuestions, baseline.openerQuestions) ||
    !arraysEqual(draft.fallbackIds, baseline.fallbackIds) ||
    !runtimePoliciesEqual(draft.runtimePolicy, baseline.runtimePolicy)
  )
}

export function mergeUntouchedAgentSettingsDraft(
  draft: AgentSettingsDraft,
  previousBaseline: AgentSettingsDraft,
  nextBaseline: AgentSettingsDraft,
): AgentSettingsDraft {
  return {
    name: draft.name === previousBaseline.name ? nextBaseline.name : draft.name,
    description:
      draft.description === previousBaseline.description
        ? nextBaseline.description
        : draft.description,
    systemPrompt:
      draft.systemPrompt === previousBaseline.systemPrompt
        ? nextBaseline.systemPrompt
        : draft.systemPrompt,
    modelId: draft.modelId === previousBaseline.modelId ? nextBaseline.modelId : draft.modelId,
    identityMode:
      draft.identityMode === previousBaseline.identityMode
        ? nextBaseline.identityMode
        : draft.identityMode,
    fallbackIds: arraysEqual(draft.fallbackIds, previousBaseline.fallbackIds)
      ? nextBaseline.fallbackIds
      : draft.fallbackIds,
    selectedToolIds: setsEqual(draft.selectedToolIds, previousBaseline.selectedToolIds)
      ? nextBaseline.selectedToolIds
      : draft.selectedToolIds,
    selectedMcpToolIds: setsEqual(draft.selectedMcpToolIds, previousBaseline.selectedMcpToolIds)
      ? nextBaseline.selectedMcpToolIds
      : draft.selectedMcpToolIds,
    selectedSkillIds: setsEqual(draft.selectedSkillIds, previousBaseline.selectedSkillIds)
      ? nextBaseline.selectedSkillIds
      : draft.selectedSkillIds,
    selectedSubAgentIds: setsEqual(draft.selectedSubAgentIds, previousBaseline.selectedSubAgentIds)
      ? nextBaseline.selectedSubAgentIds
      : draft.selectedSubAgentIds,
    temperature:
      draft.temperature === previousBaseline.temperature
        ? nextBaseline.temperature
        : draft.temperature,
    topP: draft.topP === previousBaseline.topP ? nextBaseline.topP : draft.topP,
    maxTokens:
      draft.maxTokens === previousBaseline.maxTokens ? nextBaseline.maxTokens : draft.maxTokens,
    selectedMiddlewareTypes: setsEqual(
      draft.selectedMiddlewareTypes,
      previousBaseline.selectedMiddlewareTypes,
    )
      ? nextBaseline.selectedMiddlewareTypes
      : draft.selectedMiddlewareTypes,
    openerQuestions: arraysEqual(draft.openerQuestions, previousBaseline.openerQuestions)
      ? nextBaseline.openerQuestions
      : draft.openerQuestions,
    runtimePolicy: runtimePoliciesEqual(draft.runtimePolicy, previousBaseline.runtimePolicy)
      ? nextBaseline.runtimePolicy
      : draft.runtimePolicy,
  }
}

export function buildAgentUpdateRequest(draft: AgentSettingsDraft): AgentUpdateRequest {
  return {
    name: draft.name,
    description: draft.description || undefined,
    system_prompt: draft.systemPrompt,
    model_id: draft.modelId,
    identity_mode: draft.identityMode,
    tool_ids: Array.from(draft.selectedToolIds),
    mcp_tool_ids: Array.from(draft.selectedMcpToolIds),
    skill_ids: Array.from(draft.selectedSkillIds),
    sub_agent_ids: Array.from(draft.selectedSubAgentIds),
    middleware_configs: Array.from(draft.selectedMiddlewareTypes).map((type) => ({
      type,
      params: {},
    })),
    model_params: {
      temperature: draft.temperature,
      top_p: draft.topP,
      max_tokens: draft.maxTokens,
    },
    opener_questions: draft.openerQuestions,
    model_fallback_ids: draft.fallbackIds.length > 0 ? draft.fallbackIds : null,
    runtime_policy: cloneRuntimePolicy(draft.runtimePolicy),
  }
}
