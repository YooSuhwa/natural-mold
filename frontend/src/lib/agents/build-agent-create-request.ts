import type {
  AgentCreateRequest,
  AgentIdentityMode,
  MiddlewareConfigEntry,
  RuntimePolicyV1,
} from '@/lib/types'

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
  /**
   * `undefined` keeps legacy create callers byte-for-byte compatible;
   * `null` explicitly asks the server for its recommended policy.
   */
  runtimePolicy?: RuntimePolicyV1 | null
}

/**
 * Build the existing agent-create request used by manual and visual creation.
 *
 * Untouched legacy callers omit `runtime_policy`. The manual-create advanced
 * control passes either an explicit recommended `null` or a complete policy.
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
  if (input.runtimePolicy !== undefined) {
    request.runtime_policy = input.runtimePolicy
  }
  return request
}
