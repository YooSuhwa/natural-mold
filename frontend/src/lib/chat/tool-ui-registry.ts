import { GenericToolFallback } from '@/components/chat/tool-ui/generic-tool-ui'
import { PlanToolUI } from '@/components/chat/tool-ui/plan-tool-ui'
import { UserInputUI } from '@/components/chat/tool-ui/user-input-ui'
import { ApprovalCard } from '@/components/chat/tool-ui/approval-card'
import {
  WebSearchToolUI,
  NaverBlogSearchToolUI,
  NaverNewsSearchToolUI,
  GoogleSearchToolUI,
  GoogleNewsSearchToolUI,
} from '@/components/chat/tool-ui/search-tool-ui'
import {
  ReadFileToolUI,
  WriteFileToolUI,
  EditFileToolUI,
} from '@/components/chat/tool-ui/code-tool-ui'

/** 모든 도구 UI — HiTL 포함 */
export const ALL_TOOL_UI = [
  GenericToolFallback,
  UserInputUI,
  ApprovalCard,
  PlanToolUI,
  WebSearchToolUI,
  NaverBlogSearchToolUI,
  NaverNewsSearchToolUI,
  GoogleSearchToolUI,
  GoogleNewsSearchToolUI,
  ReadFileToolUI,
  WriteFileToolUI,
  EditFileToolUI,
] as const

/** HiTL 제외 — AssistantPanel용 */
export const TOOL_UI_WITHOUT_HITL = ALL_TOOL_UI.filter(
  (ui) => ui !== UserInputUI && ui !== ApprovalCard,
)
