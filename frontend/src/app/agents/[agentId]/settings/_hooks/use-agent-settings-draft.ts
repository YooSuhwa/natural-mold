import { useEffect, useMemo, useReducer } from 'react'
import type { Agent, AgentIdentityMode } from '@/lib/types'
import { toggleSetItem } from '@/lib/utils'
import {
  buildAgentSettingsDraftFromAgent,
  buildAgentUpdateRequest,
  createEmptyAgentSettingsDraft,
  DEFAULT_MAX_TOKENS,
  DEFAULT_TEMPERATURE,
  DEFAULT_TOP_P,
  isAgentSettingsDraftDirty,
  mergeUntouchedAgentSettingsDraft,
  type AgentSettingsDraft,
} from '../_lib/agent-settings-draft'

type DraftState = {
  draft: AgentSettingsDraft
  baseline: AgentSettingsDraft | null
  agentId: string | null
}

type DraftAction =
  | { type: 'syncAgent'; agent: Agent }
  | { type: 'setName'; value: string }
  | { type: 'setDescription'; value: string }
  | { type: 'setSystemPrompt'; value: string }
  | { type: 'setModelId'; value: string }
  | { type: 'setIdentityMode'; value: AgentIdentityMode }
  | { type: 'setFallbackIds'; value: string[] }
  | { type: 'toggleTool'; id: string }
  | { type: 'toggleMcpTool'; id: string }
  | { type: 'toggleSkill'; id: string }
  | { type: 'toggleSubAgent'; id: string }
  | { type: 'setTemperature'; value: number }
  | { type: 'setTopP'; value: number }
  | { type: 'setMaxTokens'; value: number }
  | { type: 'resetModelParams' }
  | { type: 'toggleMiddleware'; middlewareType: string }
  | { type: 'setOpenerQuestions'; value: string[] }

export type AgentSettingsDraftActions = {
  setName: (value: string) => void
  setDescription: (value: string) => void
  setSystemPrompt: (value: string) => void
  setModelId: (value: string) => void
  setIdentityMode: (value: AgentIdentityMode) => void
  setFallbackIds: (value: string[]) => void
  toggleTool: (id: string) => void
  toggleMcpTool: (id: string) => void
  toggleSkill: (id: string) => void
  toggleSubAgent: (id: string) => void
  setTemperature: (value: number) => void
  setTopP: (value: number) => void
  setMaxTokens: (value: number) => void
  resetModelParams: () => void
  toggleMiddleware: (middlewareType: string) => void
  setOpenerQuestions: (value: string[]) => void
}

const INITIAL_STATE: DraftState = {
  draft: createEmptyAgentSettingsDraft(),
  baseline: null,
  agentId: null,
}

function agentSettingsDraftReducer(state: DraftState, action: DraftAction): DraftState {
  switch (action.type) {
    case 'syncAgent': {
      const nextBaseline = buildAgentSettingsDraftFromAgent(action.agent)
      if (!state.baseline || state.agentId !== action.agent.id) {
        return {
          draft: nextBaseline,
          baseline: nextBaseline,
          agentId: action.agent.id,
        }
      }
      return {
        draft: mergeUntouchedAgentSettingsDraft(state.draft, state.baseline, nextBaseline),
        baseline: nextBaseline,
        agentId: action.agent.id,
      }
    }
    case 'setName':
      return { ...state, draft: { ...state.draft, name: action.value } }
    case 'setDescription':
      return { ...state, draft: { ...state.draft, description: action.value } }
    case 'setSystemPrompt':
      return { ...state, draft: { ...state.draft, systemPrompt: action.value } }
    case 'setModelId':
      return { ...state, draft: { ...state.draft, modelId: action.value } }
    case 'setIdentityMode':
      return { ...state, draft: { ...state.draft, identityMode: action.value } }
    case 'setFallbackIds':
      return { ...state, draft: { ...state.draft, fallbackIds: [...action.value] } }
    case 'toggleTool':
      return {
        ...state,
        draft: {
          ...state.draft,
          selectedToolIds: toggleSetItem(state.draft.selectedToolIds, action.id),
        },
      }
    case 'toggleMcpTool':
      return {
        ...state,
        draft: {
          ...state.draft,
          selectedMcpToolIds: toggleSetItem(state.draft.selectedMcpToolIds, action.id),
        },
      }
    case 'toggleSkill':
      return {
        ...state,
        draft: {
          ...state.draft,
          selectedSkillIds: toggleSetItem(state.draft.selectedSkillIds, action.id),
        },
      }
    case 'toggleSubAgent':
      return {
        ...state,
        draft: {
          ...state.draft,
          selectedSubAgentIds: toggleSetItem(state.draft.selectedSubAgentIds, action.id),
        },
      }
    case 'setTemperature':
      return { ...state, draft: { ...state.draft, temperature: action.value } }
    case 'setTopP':
      return { ...state, draft: { ...state.draft, topP: action.value } }
    case 'setMaxTokens':
      return { ...state, draft: { ...state.draft, maxTokens: action.value } }
    case 'resetModelParams':
      return {
        ...state,
        draft: {
          ...state.draft,
          temperature: DEFAULT_TEMPERATURE,
          topP: DEFAULT_TOP_P,
          maxTokens: DEFAULT_MAX_TOKENS,
        },
      }
    case 'toggleMiddleware':
      return {
        ...state,
        draft: {
          ...state.draft,
          selectedMiddlewareTypes: toggleSetItem(
            state.draft.selectedMiddlewareTypes,
            action.middlewareType,
          ),
        },
      }
    case 'setOpenerQuestions':
      return { ...state, draft: { ...state.draft, openerQuestions: [...action.value] } }
  }
}

export function useAgentSettingsDraft(agent: Agent | undefined) {
  const [state, dispatch] = useReducer(agentSettingsDraftReducer, INITIAL_STATE)

  useEffect(() => {
    if (!agent) return
    dispatch({ type: 'syncAgent', agent })
  }, [agent])

  const actions = useMemo<AgentSettingsDraftActions>(
    () => ({
      setName: (value) => dispatch({ type: 'setName', value }),
      setDescription: (value) => dispatch({ type: 'setDescription', value }),
      setSystemPrompt: (value) => dispatch({ type: 'setSystemPrompt', value }),
      setModelId: (value) => dispatch({ type: 'setModelId', value }),
      setIdentityMode: (value) => dispatch({ type: 'setIdentityMode', value }),
      setFallbackIds: (value) => dispatch({ type: 'setFallbackIds', value }),
      toggleTool: (id) => dispatch({ type: 'toggleTool', id }),
      toggleMcpTool: (id) => dispatch({ type: 'toggleMcpTool', id }),
      toggleSkill: (id) => dispatch({ type: 'toggleSkill', id }),
      toggleSubAgent: (id) => dispatch({ type: 'toggleSubAgent', id }),
      setTemperature: (value) => dispatch({ type: 'setTemperature', value }),
      setTopP: (value) => dispatch({ type: 'setTopP', value }),
      setMaxTokens: (value) => dispatch({ type: 'setMaxTokens', value }),
      resetModelParams: () => dispatch({ type: 'resetModelParams' }),
      toggleMiddleware: (middlewareType) => dispatch({ type: 'toggleMiddleware', middlewareType }),
      setOpenerQuestions: (value) => dispatch({ type: 'setOpenerQuestions', value }),
    }),
    [],
  )

  const isDirty = useMemo(
    () => isAgentSettingsDraftDirty(state.draft, state.baseline),
    [state.draft, state.baseline],
  )
  const updateRequest = useMemo(() => buildAgentUpdateRequest(state.draft), [state.draft])

  return {
    draft: state.draft,
    actions,
    isDirty,
    updateRequest,
  }
}
