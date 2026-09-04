import type { AgentCreateRequest, AgentIdentityMode, MiddlewareConfigEntry } from '@/lib/types'

type AgentCreateRequestInput = {
  name: string
  description?: string
  systemPrompt: string
  modelId: string
  identityMode: AgentIdentityMode
  toolIds: Iterable<string>
  mcpToolIds: Iterable<string>
  skillIds: Iterable<string>
  subAgentIds: Iterable<string>
  middlewareTypes: Iterable<string>
  temperature: number
  topP: number
  maxTokens: number
  openerQuestions?: string[]
}

/**
 * Build the existing agent-create request used by manual and visual creation.
 *
 * New agents deliberately omit `runtime_policy`: absence is the legacy
 * compatibility state until runtime controls are introduced in a later UI
 * slice. Existing settings save through their dedicated update path so they
 * can preserve an already stored policy.
 */
export function buildAgentCreateRequest(input: AgentCreateRequestInput): AgentCreateRequest {
  const middleware_configs: MiddlewareConfigEntry[] = Array.from(input.middlewareTypes, (type) => ({
    type,
    params: {},
  }))
  const request: AgentCreateRequest = {
    name: input.name,
    description: input.description,
    system_prompt: input.systemPrompt,
    model_id: input.modelId,
    identity_mode: input.identityMode,
    tool_ids: Array.from(input.toolIds),
    mcp_tool_ids: Array.from(input.mcpToolIds),
    skill_ids: Array.from(input.skillIds),
    sub_agent_ids: Array.from(input.subAgentIds),
    middleware_configs,
    model_params: {
      temperature: input.temperature,
      top_p: input.topP,
      max_tokens: input.maxTokens,
    },
  }
  if (input.openerQuestions !== undefined) {
    request.opener_questions = input.openerQuestions
  }
  return request
}
