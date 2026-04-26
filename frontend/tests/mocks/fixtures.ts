import type {
  Agent,
  Model,
  Tool,
  Template,
  Conversation,
  Message,
  AgentTrigger,
  UsageSummary,
  CreationSession,
  Connection,
  Credential,
  BuilderSession,
  BuilderDraftConfig,
} from '@/lib/types'

// Legacy type — was in @/lib/api/creation-session (removed in v2)
export interface CreationMessageResult {
  role: string
  content: string
  current_phase: number
  phase_result: unknown
  question: string
  draft_config: {
    name: string
    description: string
    system_prompt: string
    is_ready: boolean
  } | null
  suggested_replies: { options: string[]; multi_select: boolean } | null
  recommended_tools: Array<{ name: string; description: string }>
}

// ── Agent ──────────────────────────────────────────────────────────

export const mockAgent: Agent = {
  id: 'agent-1',
  name: 'Test Agent',
  description: 'A test agent',
  system_prompt: 'You are a helpful assistant.',
  model: { id: 'model-1', display_name: 'GPT-4o' },
  tools: [{ id: 'tool-1', name: 'Web Search' }],
  skills: [],
  status: 'active',
  is_favorite: false,
  model_params: null,
  middleware_configs: [],
  template_id: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

export const mockAgentList: Agent[] = [
  mockAgent,
  {
    ...mockAgent,
    id: 'agent-2',
    name: 'Second Agent',
    description: 'Another test agent',
  },
]

// ── Model ──────────────────────────────────────────────────────────

export const mockModel: Model = {
  id: 'model-1',
  provider: 'openai',
  model_name: 'gpt-4o',
  display_name: 'GPT-4o',
  base_url: null,
  is_default: true,
  cost_per_input_token: 0.0025,
  cost_per_output_token: 0.01,
  created_at: '2026-01-01T00:00:00Z',
}

export const mockModelList: Model[] = [
  mockModel,
  {
    ...mockModel,
    id: 'model-2',
    provider: 'anthropic',
    model_name: 'claude-sonnet-4-20250514',
    display_name: 'Claude Sonnet 4',
    is_default: false,
  },
]

// ── Tool ───────────────────────────────────────────────────────────

export const mockTool: Tool = {
  id: 'tool-1',
  type: 'prebuilt',
  is_system: true,
  provider_name: 'naver',
  name: 'Web Search',
  description: 'Search the web using DuckDuckGo',
  parameters_schema: null,
  api_url: null,
  http_method: null,
  auth_type: null,
  tags: ['search', 'web', 'free'],
  connection_id: null,
  agent_count: 1,
  created_at: '2026-01-01T00:00:00Z',
}

export const mockToolList: Tool[] = [
  mockTool,
  {
    ...mockTool,
    id: 'tool-2',
    type: 'custom',
    is_system: false,
    provider_name: null,
    name: 'My Custom API',
    description: 'A custom tool',
    api_url: 'https://example.com/api',
    http_method: 'POST',
    connection_id: 'conn-custom-1',
  },
]

// ── Connection (ADR-008) ───────────────────────────────────────────

export const mockCustomConnection: Connection = {
  id: 'conn-custom-1',
  user_id: 'user-1',
  type: 'custom',
  provider_name: 'custom_api_key',
  display_name: 'My Custom API',
  credential_id: 'cred-1',
  extra_config: null,
  is_default: false,
  status: 'active',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

export const mockMcpConnection: Connection = {
  id: 'conn-mcp-1',
  user_id: 'user-1',
  type: 'mcp',
  provider_name: 'mcp_custom',
  display_name: 'Test MCP',
  credential_id: 'cred-2',
  extra_config: {
    url: 'https://example.com/mcp',
    auth_type: 'bearer',
    header_keys: null,
    env_var_keys: ['Authorization'],
    transport: null,
    timeout: null,
  },
  is_default: false,
  status: 'active',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

export const mockConnectionList: Connection[] = [mockCustomConnection, mockMcpConnection]

// ── Credential ─────────────────────────────────────────────────────

export const mockCredential: Credential = {
  id: 'cred-1',
  name: 'My API Key',
  credential_type: 'api_key',
  provider_name: 'custom_api_key',
  is_active: true,
  has_data: true,
  field_keys: ['api_key'],
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

export const mockCredentialList: Credential[] = [
  mockCredential,
  {
    ...mockCredential,
    id: 'cred-2',
    name: 'MCP Bearer',
    field_keys: ['api_key'],
  },
]

// ── Template ───────────────────────────────────────────────────────

export const mockTemplate: Template = {
  id: 'template-1',
  name: 'Research Assistant',
  description: 'An agent that helps with research',
  category: 'productivity',
  system_prompt: 'You are a research assistant.',
  recommended_tools: ['Web Search'],
  recommended_model_id: 'model-1',
  usage_example: 'Find the latest news about AI',
  created_at: '2026-01-01T00:00:00Z',
}

export const mockTemplateList: Template[] = [
  mockTemplate,
  {
    ...mockTemplate,
    id: 'template-2',
    name: 'Writing Helper',
    category: 'creative',
    description: 'Helps with writing tasks',
  },
]

// ── Conversation & Message ─────────────────────────────────────────

export const mockConversation: Conversation = {
  id: 'conv-1',
  agent_id: 'agent-1',
  title: 'Test Conversation',
  is_pinned: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

export const mockConversationList: Conversation[] = [
  mockConversation,
  {
    ...mockConversation,
    id: 'conv-2',
    title: 'Second Conversation',
  },
]

export const mockMessage: Message = {
  id: 'msg-1',
  conversation_id: 'conv-1',
  role: 'user',
  content: 'Hello, how are you?',
  tool_calls: null,
  tool_call_id: null,
  created_at: '2026-01-01T00:00:00Z',
}

export const mockMessageList: Message[] = [
  mockMessage,
  {
    id: 'msg-2',
    conversation_id: 'conv-1',
    role: 'assistant',
    content: "I'm doing great! How can I help?",
    tool_calls: null,
    tool_call_id: null,
    created_at: '2026-01-01T00:00:01Z',
  },
]

// ── Trigger ────────────────────────────────────────────────────────

export const mockTrigger: AgentTrigger = {
  id: 'trigger-1',
  agent_id: 'agent-1',
  trigger_type: 'interval',
  schedule_config: { interval_minutes: 60 },
  input_message: 'Check for updates',
  status: 'active',
  last_run_at: null,
  next_run_at: '2026-01-01T01:00:00Z',
  run_count: 0,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

export const mockTriggerList: AgentTrigger[] = [
  mockTrigger,
  {
    ...mockTrigger,
    id: 'trigger-2',
    trigger_type: 'cron',
    schedule_config: { cron_expression: '0 9 * * *' },
    input_message: 'Good morning report',
    status: 'paused',
  },
]

// ── Usage ──────────────────────────────────────────────────────────

export const mockUsageSummary: UsageSummary = {
  period: '7d',
  total_tokens: 150000,
  prompt_tokens: 100000,
  completion_tokens: 50000,
  estimated_cost_usd: 1.25,
  by_agent: [
    {
      agent_id: 'agent-1',
      agent_name: 'Test Agent',
      total_tokens: 100000,
      estimated_cost: 0.85,
    },
    {
      agent_id: 'agent-2',
      agent_name: 'Second Agent',
      total_tokens: 50000,
      estimated_cost: 0.4,
    },
  ],
}

// ── Creation Session ───────────────────────────────────────────────

export const mockCreationSession: CreationSession = {
  id: 'session-1',
  status: 'in_progress',
  conversation_history: [
    { role: 'assistant', content: 'What kind of agent would you like to create?' },
  ],
  draft_config: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

export const mockCreationMessageResult: CreationMessageResult = {
  role: 'assistant',
  content: 'Great! Let me help you set that up.',
  current_phase: 2,
  phase_result: null,
  question: 'What tools should the agent have?',
  draft_config: {
    name: 'My Agent',
    description: 'A helpful agent',
    system_prompt: 'You are helpful.',
    is_ready: false,
  },
  suggested_replies: { options: ['Web Search', 'Custom Tool'], multi_select: true },
  recommended_tools: [{ name: 'Web Search', description: 'Search the web' }],
}

// ── Builder v2 ────────────────────────────────────────────────────

export const mockBuilderDraftConfig: BuilderDraftConfig = {
  name: 'News Agent',
  name_ko: '뉴스 에이전트',
  description: 'Summarizes daily news',
  system_prompt: 'You are a news summarizer.',
  tools: ['Web Search', 'Web Scraper'],
  middlewares: [],
  model_name: 'gpt-4o',
  primary_task_type: 'research',
  use_cases: ['Daily news digest'],
}

export const mockBuilderSession: BuilderSession = {
  id: 'builder-session-1',
  status: 'preview',
  current_phase: 7,
  user_request: '뉴스 요약 에이전트',
  intent: {
    agent_name: 'News Agent',
    agent_name_ko: '뉴스 에이전트',
    agent_description: 'Summarizes daily news',
    primary_task_type: 'research',
    tool_preferences: 'web search',
    output_style: 'summary',
    response_tone: 'formal',
    use_cases: ['Daily news digest'],
    constraints: [],
    required_capabilities: ['web_search'],
  },
  tools_result: [
    { tool_name: 'Web Search', description: 'Search the web', reason: 'Required for news' },
  ],
  middlewares_result: [],
  system_prompt: 'You are a news summarizer.',
  draft_config: mockBuilderDraftConfig,
  agent_id: null,
  error_message: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}
