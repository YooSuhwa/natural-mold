import {
  BookOpenIcon,
  BrainIcon,
  CalendarIcon,
  ClockIcon,
  FilePenIcon,
  FilePlusIcon,
  FileTextIcon,
  FolderIcon,
  GlobeIcon,
  ListChecksIcon,
  MailIcon,
  MessageCircleIcon,
  MessageSquareIcon,
  SearchIcon,
  ShieldCheckIcon,
  UsersIcon,
  WrenchIcon,
  type LucideIcon,
} from 'lucide-react'

// ──────────────────────────────────────────────
// toolIcon — 채팅 도구 pill/그룹 헤더의 leading 아이콘 해석.
//
// 런타임 주입 빌트인 + 알려진 registry 도구는 "미리 정해진 세트"라 이름 기준
// 고정 매핑을 둔다. 매핑에 없는 도구(임의 MCP 등)는 generic 렌치로 폴백.
// (도구가 가진 backend ``icon_id``를 채팅까지 노출하는 건 후속 — 현재 ToolBrief는
// icon_id를 싣지 않는다.)
// ──────────────────────────────────────────────

const EXACT_TOOL_ICONS: Readonly<Record<string, LucideIcon>> = {
  // temporal
  current_datetime: ClockIcon,
  resolve_relative_date: ClockIcon,
  // web / search
  web_search: SearchIcon,
  tavily_search: SearchIcon,
  google_search: SearchIcon,
  google_news_search: SearchIcon,
  web_scraper: GlobeIcon,
  http_request: GlobeIcon,
  // files (deepagents virtual FS)
  read_file: FileTextIcon,
  write_file: FilePlusIcon,
  edit_file: FilePenIcon,
  ls: FolderIcon,
  // planning / skills / delegation
  write_todos: ListChecksIcon,
  execute_in_skill: BookOpenIcon,
  task: UsersIcon,
  // HiTL
  ask_user: MessageCircleIcon,
  ask_clarifying_question: MessageCircleIcon,
  request_approval: ShieldCheckIcon,
  // google workspace
  gmail_send: MailIcon,
  google_calendar_event: CalendarIcon,
  google_chat_webhook: MessageSquareIcon,
  // memory
  propose_memory: BrainIcon,
  save_user_memory: BrainIcon,
  save_agent_memory: BrainIcon,
}

/** 접두사 매핑 — 같은 계열의 변종(naver_blog_search 등)을 한 번에. */
const PREFIX_TOOL_ICONS: ReadonlyArray<readonly [string, LucideIcon]> = [
  ['naver_', SearchIcon],
  ['google_', SearchIcon],
]

/**
 * toolName → 빌트인 고정 맵 아이콘. 매핑에 없으면 null(호출 측이 도구 icon_id나
 * 렌치로 폴백). 런타임 주입 빌트인은 agent.tools에 없어 icon_id가 없으므로 이
 * 고정 맵이 1순위다.
 */
export function builtinToolIcon(toolName: string): LucideIcon | null {
  // Defensive: a tool fallback can render before its name resolves (undefined),
  // which previously threw on ``.startsWith``. Treat a missing name as unmapped.
  if (!toolName) return null
  const exact = EXACT_TOOL_ICONS[toolName]
  if (exact) return exact
  for (const [prefix, icon] of PREFIX_TOOL_ICONS) {
    if (toolName.startsWith(prefix)) return icon
  }
  return null
}

/** toolName → leading 아이콘. 알려진 빌트인이면 의미 아이콘, 아니면 렌치 폴백.
 * (icon_id까지 고려하려면 컴포넌트에서 ``useToolIcon`` 훅을 쓴다.) */
export function toolIcon(toolName: string): LucideIcon {
  return builtinToolIcon(toolName) ?? WrenchIcon
}
