import type { Agent } from '@/lib/types'
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
} from '@/app/agents/[agentId]/settings/_lib/agent-settings-draft'

function makeAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: 'agent-1',
    runtime_name: 'agent_1',
    identity_mode: 'per_user',
    name: 'Research Agent',
    description: null,
    system_prompt: 'You are a researcher.',
    model: { id: 'model-primary', display_name: 'GPT-4o mini' },
    tools: [],
    mcp_tools: [],
    skills: [],
    sub_agents: [],
    status: 'active',
    is_favorite: false,
    model_params: null,
    middleware_configs: [],
    template_id: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    image_url: null,
    opener_questions: null,
    llm_credential_id: null,
    unread_count: 0,
    model_fallback_ids: null,
    ...overrides,
  }
}

describe('agent settings draft helpers', () => {
  it('normalizes nullable agent fields into editable draft defaults', () => {
    const draft = buildAgentSettingsDraftFromAgent(makeAgent())

    expect(draft.description).toBe('')
    expect(draft.fallbackIds).toEqual([])
    expect(draft.selectedMcpToolIds.size).toBe(0)
    expect(draft.openerQuestions).toEqual([])
    expect(draft.temperature).toBe(DEFAULT_TEMPERATURE)
    expect(draft.topP).toBe(DEFAULT_TOP_P)
    expect(draft.maxTokens).toBe(DEFAULT_MAX_TOKENS)
  })

  it('treats MCP tools, fallback ids, and opener questions as dirty fields', () => {
    const baseline = buildAgentSettingsDraftFromAgent(
      makeAgent({
        mcp_tools: [{ id: 'mcp-tool-1', name: 'Repo Search', server_id: 'server-1' }],
        model_fallback_ids: ['model-fallback-a'],
        opener_questions: ['What should I research?'],
      }),
    )

    expect(
      isAgentSettingsDraftDirty(
        { ...baseline, selectedMcpToolIds: new Set(['mcp-tool-2']) },
        baseline,
      ),
    ).toBe(true)
    expect(
      isAgentSettingsDraftDirty({ ...baseline, fallbackIds: ['model-fallback-b'] }, baseline),
    ).toBe(true)
    expect(
      isAgentSettingsDraftDirty(
        { ...baseline, openerQuestions: ['Summarize this document.'] },
        baseline,
      ),
    ).toBe(true)
  })

  it('merges refetched server values only into untouched draft fields', () => {
    const previousBaseline = buildAgentSettingsDraftFromAgent(
      makeAgent({
        name: 'Server Name',
        description: 'Old server description',
        system_prompt: 'Old server prompt',
      }),
    )
    const currentDraft: AgentSettingsDraft = {
      ...previousBaseline,
      name: 'Local edited name',
      selectedMcpToolIds: new Set(['local-mcp-tool']),
    }
    const nextBaseline = buildAgentSettingsDraftFromAgent(
      makeAgent({
        name: 'Refetched server name',
        description: 'New server description',
        system_prompt: 'New server prompt',
        model_params: { temperature: 0.2, top_p: 0.8, max_tokens: 1024 },
        mcp_tools: [{ id: 'server-mcp-tool', name: 'Server MCP', server_id: 'server-1' }],
      }),
    )

    const merged = mergeUntouchedAgentSettingsDraft(
      currentDraft,
      previousBaseline,
      nextBaseline,
    )

    expect(merged.name).toBe('Local edited name')
    expect(Array.from(merged.selectedMcpToolIds)).toEqual(['local-mcp-tool'])
    expect(merged.description).toBe('New server description')
    expect(merged.systemPrompt).toBe('New server prompt')
    expect(merged.temperature).toBe(0.2)
    expect(merged.topP).toBe(0.8)
    expect(merged.maxTokens).toBe(1024)
  })

  it('builds the same complete agent update request shape used by the settings page', () => {
    const draft = buildAgentSettingsDraftFromAgent(
      makeAgent({
        description: 'A useful assistant',
        tools: [{ id: 'tool-1', name: 'Web Search' }],
        mcp_tools: [{ id: 'mcp-tool-1', name: 'Repo Search', server_id: 'server-1' }],
        skills: [{ id: 'skill-1', name: 'Research Skill' }],
        sub_agents: [{ id: 'sub-agent-1', name: 'Helper Agent', description: null }],
        middleware_configs: [{ type: 'human_in_the_loop', params: { ignored: true } }],
        model_params: { temperature: 0.4, top_p: 0.9, max_tokens: 2048 },
        opener_questions: ['Start here'],
        model_fallback_ids: ['model-fallback-a'],
      }),
    )

    expect(buildAgentUpdateRequest(draft)).toEqual({
      name: 'Research Agent',
      description: 'A useful assistant',
      system_prompt: 'You are a researcher.',
      model_id: 'model-primary',
      identity_mode: 'per_user',
      tool_ids: ['tool-1'],
      mcp_tool_ids: ['mcp-tool-1'],
      skill_ids: ['skill-1'],
      sub_agent_ids: ['sub-agent-1'],
      middleware_configs: [{ type: 'human_in_the_loop', params: {} }],
      model_params: { temperature: 0.4, top_p: 0.9, max_tokens: 2048 },
      opener_questions: ['Start here'],
      model_fallback_ids: ['model-fallback-a'],
    })
  })

  it('serializes empty optional fields the same way as the previous page implementation', () => {
    const request = buildAgentUpdateRequest(createEmptyAgentSettingsDraft())

    expect(request.description).toBeUndefined()
    expect(request.model_fallback_ids).toBeNull()
  })
})
