import {
  defineToolkit,
  Tools,
  type Toolkit,
  type ToolCallMessagePartComponent,
} from '@assistant-ui/react'
import { createMoldyMcpAppRenderer } from '@/lib/chat/mcp-apps/renderer'
import { PlanToolUI } from '@/components/chat/tool-ui/plan-tool-ui'
import { UserInputUI } from '@/components/chat/tool-ui/user-input-ui'
import { ApprovalCard } from '@/components/chat/tool-ui/approval-card'
import { ClarifyingQuestionUI } from '@/components/chat/tool-ui/clarifying-question-ui'
import { SearchToolUI } from '@/components/chat/tool-ui/search-tool-ui'
import {
  ReadFileToolUI,
  WriteFileToolUI,
  EditFileToolUI,
} from '@/components/chat/tool-ui/code-tool-ui'
import { PhaseTimelineToolUI } from '@/components/chat/tool-ui/phase-timeline-ui'
import { RecommendationApprovalToolUI } from '@/components/chat/tool-ui/recommendation-approval-ui'
import { PromptApprovalToolUI } from '@/components/chat/tool-ui/prompt-approval-ui'
import {
  ImageChoiceToolUI,
  ImageApprovalToolUI,
} from '@/components/chat/tool-ui/image-generation-ui'
import {
  DraftConfigCardToolUI,
  DraftApprovalToolUI,
} from '@/components/chat/tool-ui/draft-config-ui'
import { SubAgentToolUI } from '@/components/chat/tool-ui/sub-agent-ui'
import { SkillExecutionToolUI } from '@/components/chat/tool-ui/skill-execution-ui'
import {
  ProposeMemoryToolUI,
  SaveAgentMemoryToolUI,
  SaveUserMemoryToolUI,
} from '@/components/chat/tool-ui/memory-tool-ui'

function backendRenderer<TArgs, TResult>(render: ToolCallMessagePartComponent<TArgs, TResult>) {
  return { type: 'backend' as const, render }
}

const SEARCH_TOOLKIT = {
  tavily_search: backendRenderer(SearchToolUI),
  web_search: backendRenderer(SearchToolUI),
  naver_search_blog: backendRenderer(SearchToolUI),
  naver_search_news: backendRenderer(SearchToolUI),
  naver_search_image: backendRenderer(SearchToolUI),
  naver_search_shop: backendRenderer(SearchToolUI),
  naver_search_local: backendRenderer(SearchToolUI),
  google_search_web: backendRenderer(SearchToolUI),
  google_search_image: backendRenderer(SearchToolUI),
  google_search_news: backendRenderer(SearchToolUI),
  naver_blog_search: backendRenderer(SearchToolUI),
  naver_news_search: backendRenderer(SearchToolUI),
  google_search: backendRenderer(SearchToolUI),
  google_news_search: backendRenderer(SearchToolUI),
}

const COMMON_TOOLKIT = {
  ask_clarifying_question: backendRenderer(ClarifyingQuestionUI),
  write_todos: backendRenderer(PlanToolUI),
  task: backendRenderer(SubAgentToolUI),
  execute_in_skill: backendRenderer(SkillExecutionToolUI),
  propose_memory: backendRenderer(ProposeMemoryToolUI),
  save_user_memory: backendRenderer(SaveUserMemoryToolUI),
  save_agent_memory: backendRenderer(SaveAgentMemoryToolUI),
  ...SEARCH_TOOLKIT,
  read_file: backendRenderer(ReadFileToolUI),
  write_file: backendRenderer(WriteFileToolUI),
  edit_file: backendRenderer(EditFileToolUI),
}

/** Main chat and AssistantPanel policy: all domain renderers, including HITL approvals. */
export const ALL_TOOLKIT = defineToolkit({
  ask_user: backendRenderer(UserInputUI),
  request_approval: backendRenderer(ApprovalCard),
  ...COMMON_TOOLKIT,
})

/** Settings test chat policy: ordinary domain renderers without paused-run HITL controls. */
export const SETTINGS_TEST_TOOLKIT = defineToolkit(COMMON_TOOLKIT)

/** Builder v3 policy: builder controls and approvals plus the shared ask_user flow. */
export const BUILDER_TOOLKIT = defineToolkit({
  ask_user: backendRenderer(UserInputUI),
  phase_timeline: backendRenderer(PhaseTimelineToolUI),
  recommendation_approval: backendRenderer(RecommendationApprovalToolUI),
  prompt_approval: backendRenderer(PromptApprovalToolUI),
  image_choice: backendRenderer(ImageChoiceToolUI),
  image_approval: backendRenderer(ImageApprovalToolUI),
  draft_config_card: backendRenderer(DraftConfigCardToolUI),
  draft_approval: backendRenderer(DraftApprovalToolUI),
})

/** Main-thread Tools resource with MCP Apps bound to the mounted conversation. */
export function createMoldyChatTools(toolkit: Toolkit, conversationId?: string) {
  return Tools({
    toolkit,
    ...(conversationId ? { mcpApp: createMoldyMcpAppRenderer({ conversationId }) } : {}),
  })
}
