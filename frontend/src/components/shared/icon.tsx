'use client'

import {
  Blocks,
  BookOpen,
  Bot,
  Box,
  Brain,
  CalendarClock,
  Plug,
  Code,
  Globe,
  Search,
  Mail,
  Calendar,
  MessageSquare,
  Image,
  Sparkles,
  KeyRound,
  Server,
  Database,
  Wrench,
  FileText,
  Lock,
  Cloud,
  HardDrive,
  Package,
  Route,
  ShoppingBag,
  Store,
  TrainFront,
  TreePine,
  Users,
  Workflow,
  type LucideIcon,
} from 'lucide-react'
import { createElement } from 'react'
import { cn } from '@/lib/utils'

// Icon registry — domain `icon_id` → Lucide icon. We never ship third-party
// brand assets; provider-specific IDs map to category-appropriate Lucide
// glyphs so the UI stays visually consistent and bundle-friendly.
export const DOMAIN_ICONS = {
  // generic
  box: Box,
  plug: Plug,
  code: Code,
  globe: Globe,
  cloud: Cloud,
  database: Database,
  wrench: Wrench,
  tool: Wrench,
  tools: Wrench,
  server: Server,
  file: FileText,
  lock: Lock,
  key: KeyRound,
  sparkles: Sparkles,
  bot: Bot,
  agent: Bot,
  skill: BookOpen,
  book: BookOpen,
  package: Package,
  workflow: Workflow,
  marketplace: Store,
  store: Store,
  image: Image,
  blocks: Blocks,
  middleware: Blocks,
  users: Users,
  subagent: Users,
  brain: Brain,
  model: Brain,
  models: Brain,
  storage: HardDrive,
  filesystem: HardDrive,
  // categories
  search: Search,
  mail: Mail,
  email: Mail,
  calendar: Calendar,
  chat: MessageSquare,
  message: MessageSquare,
  communication: MessageSquare,
  scheduler: CalendarClock,
  schedule: CalendarClock,
  trigger: CalendarClock,
  automation: Workflow,
  credential: KeyRound,
  credentials: KeyRound,
  mcp: Server,
  mcp_server: Server,
  mcp_tool: Plug,
  webhook: Globe,
  oauth: KeyRound,
  registry: Database,
  builtin: Wrench,
  skill_text: BookOpen,
  skill_package: Package,
  resource_agent: Bot,
  resource_skill: BookOpen,
  resource_mcp: Server,
  stdio: Code,
  sse: Globe,
  streamable_http: Globe,
  // common credential definitions
  openai: Sparkles,
  anthropic: Brain,
  google: Globe,
  google_genai: Sparkles,
  azure: Cloud,
  azure_openai: Cloud,
  openrouter: Plug,
  openai_compatible: Plug,
  google_workspace_oauth2: Mail,
  gmail: Mail,
  google_calendar: Calendar,
  google_chat: MessageSquare,
  google_search: Search,
  naver: Search,
  naver_search: Search,
  dart_api: FileText,
  document: FileText,
  coupang_partners: ShoppingBag,
  shopping: ShoppingBag,
  odsay_api: Route,
  route: Route,
  srt_account: TrainFront,
  ktx_account: TrainFront,
  train: TrainFront,
  foresttrip_account: TreePine,
  tree: TreePine,
  http_bearer: KeyRound,
  http_api_key: KeyRound,
  http_basic: Lock,
  mcp_oauth2: Server,
  http_request: Globe,
  // MCP registry provider IDs map to neutral Lucide categories.
  github: Code,
  linear: Workflow,
  jira: Workflow,
  slack: MessageSquare,
  notion: FileText,
} satisfies Record<string, LucideIcon>

export type DomainIconId = keyof typeof DOMAIN_ICONS

export const DOMAIN_ICON_OPTIONS: Array<{ id: DomainIconId; label: string }> = [
  { id: 'agent', label: 'Agent' },
  { id: 'skill', label: 'Skill' },
  { id: 'skill_package', label: 'Package' },
  { id: 'mcp', label: 'MCP' },
  { id: 'tool', label: 'Tool' },
  { id: 'credential', label: 'Credential' },
  { id: 'model', label: 'Model' },
  { id: 'scheduler', label: 'Schedule' },
  { id: 'marketplace', label: 'Marketplace' },
  { id: 'image', label: 'Image' },
  { id: 'middleware', label: 'Middleware' },
  { id: 'subagent', label: 'Subagent' },
  { id: 'search', label: 'Search' },
  { id: 'mail', label: 'Mail' },
  { id: 'calendar', label: 'Calendar' },
  { id: 'webhook', label: 'Webhook' },
  { id: 'storage', label: 'Storage' },
]

export function getDomainIcon(
  iconId?: string | null,
  fallback: DomainIconId = 'box',
): LucideIcon {
  return (iconId ? DOMAIN_ICONS[iconId as DomainIconId] : undefined) ?? DOMAIN_ICONS[fallback] ?? Box
}

export function getDomainIconIdForResource(resourceType?: string | null): DomainIconId {
  if (resourceType === 'agent') return 'agent'
  if (resourceType === 'skill') return 'skill'
  if (resourceType === 'mcp') return 'mcp'
  return 'box'
}

export function getDomainIconIdForSkillKind(kind?: string | null): DomainIconId {
  if (kind === 'package') return 'skill_package'
  if (kind === 'text') return 'skill_text'
  return 'skill'
}

export function getDomainIconIdForMcpTransport(transport?: string | null): DomainIconId {
  if (transport === 'stdio') return 'stdio'
  if (transport === 'sse') return 'sse'
  if (transport === 'streamable_http') return 'streamable_http'
  return 'mcp'
}

interface DomainIconProps {
  iconId?: string | null
  fallback?: DomainIconId
  className?: string
  size?: number
}

export function DomainIcon({
  iconId,
  fallback = 'box',
  className,
  size,
}: DomainIconProps) {
  const Icon = getDomainIcon(iconId, fallback)
  return createElement(Icon, {
    className: cn('size-4 text-foreground/80', className),
    width: size,
    height: size,
    'aria-hidden': true,
  })
}

interface EmptyStateIconProps {
  iconId?: string | null
  fallback?: DomainIconId
  className?: string
  iconClassName?: string
}

export function EmptyStateIcon({
  iconId,
  fallback = 'box',
  className,
  iconClassName,
}: EmptyStateIconProps) {
  return (
    <div
      className={cn(
        'flex size-12 items-center justify-center rounded-full bg-muted text-muted-foreground',
        className,
      )}
    >
      <DomainIcon
        iconId={iconId}
        fallback={fallback}
        className={cn('size-6 text-muted-foreground', iconClassName)}
      />
    </div>
  )
}

interface DomainIconTileProps {
  iconId?: string | null
  fallback?: DomainIconId
  className?: string
  iconClassName?: string
}

export function DomainIconTile({
  iconId,
  fallback = 'box',
  className,
  iconClassName,
}: DomainIconTileProps) {
  return (
    <div
      className={cn(
        'flex size-9 shrink-0 items-center justify-center rounded-md bg-primary/15 text-primary-strong',
        className,
      )}
    >
      <DomainIcon
        iconId={iconId}
        fallback={fallback}
        className={cn('size-5 text-primary-strong', iconClassName)}
      />
    </div>
  )
}

interface DomainIconPickerProps {
  value?: DomainIconId | string | null
  onChange: (value: DomainIconId) => void
  options?: Array<{ id: DomainIconId; label: string }>
  disabled?: boolean
  className?: string
}

export function DomainIconPicker({
  value,
  onChange,
  options = DOMAIN_ICON_OPTIONS,
  disabled = false,
  className,
}: DomainIconPickerProps) {
  return (
    <div
      role="radiogroup"
      className={cn('grid grid-cols-6 gap-1.5 sm:grid-cols-9', className)}
    >
      {options.map((option) => {
        const selected = value === option.id
        return (
          <button
            key={option.id}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-label={option.label}
            title={option.label}
            disabled={disabled}
            onClick={() => onChange(option.id)}
            className={cn(
              'flex size-9 items-center justify-center rounded-md border text-muted-foreground transition-colors',
              'hover:border-primary/40 hover:bg-muted hover:text-foreground',
              'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
              selected &&
                'border-primary/40 bg-primary/15 text-primary-strong ring-1 ring-primary/25',
              disabled && 'cursor-not-allowed opacity-50',
            )}
          >
            <DomainIcon
              iconId={option.id}
              className={cn('size-4', selected ? 'text-primary-strong' : 'text-current')}
            />
          </button>
        )
      })}
    </div>
  )
}
