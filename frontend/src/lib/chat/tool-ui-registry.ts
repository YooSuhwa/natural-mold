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

/** Builder v3 전용 — 8-phase 빌더 흐름의 Tool UI 5종 + 공통 ask_user */
export const BUILDER_TOOL_UI = [
  GenericToolFallback,
  UserInputUI,
  PhaseTimelineToolUI,
  RecommendationApprovalToolUI,
  PromptApprovalToolUI,
  ImageChoiceToolUI,
  ImageApprovalToolUI,
  DraftConfigCardToolUI,
  DraftApprovalToolUI,
] as const
